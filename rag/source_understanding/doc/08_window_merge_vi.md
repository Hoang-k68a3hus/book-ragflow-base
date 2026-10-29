# 08 — Hợp nhất nhiều windows, giữ occurrences và remap links

Hợp đồng: `edu-annotation-1.1-pilot`. Đặc tả này áp dụng cho **bản ghi dẫn xuất của dataset**, chưa là một stage được nối vào runtime Python. Bản tham chiếu chạy độc lập tại [merge_windows.mjs](examples/merge_windows.mjs); các ca kiểm chứng tại [validate_window_merge.mjs](examples/validate_window_merge.mjs).

## 1. Điều kiện đầu vào

Một batch merge chỉ có một source revision, một target và một phiên bản guideline. Bộ điều phối cung cấp:

```text
contract_version
source = { document_id, content_hash, source_revision }
target_id
tasks = các nhiệm vụ phải phủ ở cấp target
elements = [{ element_id, text_view, text }] theo thứ tự nguồn
windows = [{ input, output }] với item_id duy nhất
```

`elements` là toàn bộ text view của các phần tử thuộc target, lấy từ canonical document có thẩm quyền; không phải text do LLM trả. `document_id` và `content_hash` không được null. Revision có thể null nếu hệ thống nguồn không quản lý revision, nhưng phải đồng nhất trong batch.

Mỗi window giữ contract input/output ở 04. `item_id` định danh duy nhất window đã được chọn sau retry. Không đưa hai phản hồi retry có cùng item ID vào cùng batch; workflow phải chọn phản hồi đã duyệt hoặc cấp run ID bên ngoài, không để merger tự chọn phản hồi cuối.

Đầu vào khác document/hash/revision/target/version bị từ chối toàn batch. Mỗi target segment và evidence phải được đối chiếu với text view đầy đủ ở `elements`. Context ngoài target không có full text trong bundle này: builder phải đối chiếu nó với canonical document trước lượt LLM; bản merge chỉ kiểm tra mapping context trong request, không chứng thực nội dung context ngoài snapshot. Không merge `RAW_TEXT` và `NORMALIZED_TEXT` của cùng element bằng phép cộng offset; bản tham chiếu yêu cầu một view thống nhất cho mỗi element trong batch.

## 2. Identity và khóa dedup

### 2.1. Identity cục bộ

Identity annotation đầu vào là tuple **`(item_id, local_annotation_id)`**, ví dụ `("w01", "m001")`. Hai windows có `m001` không phải cùng annotation. Dùng tuple/array được serialize, không nối chuỗi bằng dấu `:` có thể xuất hiện trong ID.

### 2.2. Khóa nguồn

```text
source_key = [contract_version, document_id, content_hash,
              source_revision, target_id]
span_key = [element_id, text_view, start_char, end_char, quoted_text]
mention_key = [source_key, "MENTIONS", label, span_key]
role_key = [source_key, "ROLES", label, ordered_list_of_span_keys]
```

Tạo ID dẫn xuất bằng SHA-256 của JSON encoding UTF-8 của các **array theo đúng thứ tự trên**. Không normalize Unicode hoặc casefold surface trước hash. Prefix `ma_` cho mention, `ra_` cho role; bản tham chiếu dùng đủ 64 hex ký tự. Khi cùng hash nhưng payload khác, phải báo lỗi collision, không overwrite.

Hai candidates chỉ được dedup tự động khi key giống hệt. Cùng chữ khác offset là hai occurrences khác. Cùng offset khác nhãn là xung đột. Role có evidence khác ranh giới không tự gộp dù cùng nhãn.

## 3. Thuật toán bắt buộc

### Bước A — Validate và đưa về source coordinates

1. Validate schema và bất biến từng window bằng validator ở 04.
2. Kiểm tra source identity và membership với target gốc.
3. Đối chiếu segment/evidence với full text view. Offset output đã ở element coordinates: **không cộng `element_start` lần thứ hai**.
4. Sắp evidence theo thứ tự element gốc và offset; từ chối evidence lộn thứ tự. Không sắp theo item ID hoặc thứ tự windows hoàn thành.
5. Giữ nguyên output gốc, bao gồm issues/truncation; merge tạo artifact dẫn xuất riêng.

### Bước B — Gom annotation chính xác và giữ provenance

Gom theo `mention_key`/`role_key`. Mỗi group giữ một annotation và danh sách đầy đủ origins `(item_id, local_id)`. Đánh ID nguồn ổn định từ key. Đây là dedup candidates, chưa phải xác nhận nhãn đúng chuyên môn.

### Bước C — Phát hiện xung đột, không tự chọn người thắng

| Tình huống | Hành vi |
|---|---|
| Cùng nhãn, cùng toàn bộ evidence | Dedup; giữ mọi origin |
| Cùng surface, khác vị trí | Giữ cả hai occurrences |
| Cùng phạm vi nhưng khác nhãn | `LABEL_CONFLICT`, cách ly cả hai candidates |
| Hai spans khác nhau chồng lấn trong cùng lớp | `BOUNDARY_CONFLICT`, cách ly cả hai; không chọn span dài nhất |
| Role ở hai windows chỉ trùng/tiếp giáp một phần | Không nối thành role dài; overlap là xung đột, tiếp giáp không đủ chứng minh cùng role |
| Một window gán candidate, window khác nhìn thấy đầy đủ cùng vùng và task nhưng không gán | `PRESENCE_CONFLICT` nếu window thứ hai không có issue ngữ nghĩa tại lượt đó; đưa đi duyệt |
| Một window bị cắt mất một phần candidate | Không xem sự vắng mặt trong window đó là bất đồng âm tính |

“Cùng phạm vi” được so trên element ID + text view + khoảng nguồn, không trên chuỗi bề mặt. Không so overlap giữa mentions và roles vì hai lớp được phép bao nhau.

Bản tham chiếu xử lý thận trọng: window có issue ngữ nghĩa hoặc `ABSTAIN` không được dùng làm bằng chứng âm tính cho presence. Chỉ có `TRUNCATED_TARGET` nhưng nhìn thấy trọn candidate thì vẫn được so. Các xung đột giữ candidates trong `quarantined_annotations`, không làm chúng biến thành O trong dataset train.

### Bước D — Xây mapping local → merged

Tạo một entry cho từng local mention/role:

```text
{ item_id, local_id, candidate_id, merged_id }
```

`candidate_id` là ID theo source key. `merged_id=candidate_id` nếu candidate không bị cách ly; `merged_id=null` nếu còn xung đột. Mapping là một danh sách các tuples có tên trường, không phải object chỉ keyed bằng `m001`.

Local link IDs được truy vết trong `witnesses` của merged link hoặc `pending_links`, không nhập vào mapping endpoints mentions/roles.

### Bước E — Remap links sau khi chốt candidates

1. Với link ở window W, lookup hai endpoints bằng `(W.item_id, source_id)` và `(W.item_id, target_id)`.
2. Nếu endpoint không tồn tại, input không hợp lệ. Nếu endpoint bị cách ly, đưa link vào `pending_links`, không xuất edge có endpoint treo.
3. Dùng merged endpoint IDs và type tạo key `[source_key, type, merged_source_id, merged_target_id]`; ID prefix `la_` + SHA-256.
4. Các links cùng key được gom nhưng giữ từng evidence witness và origin riêng. Không hợp các quote thành một đoạn text mới. Witness phải đủ hai endpoints và marker/tham chiếu của quan hệ như 02.
5. Nếu `ALIAS_OF` và `TRANSLATION_OF` cạnh tranh trên cùng cặp endpoint, cách ly các links đó bằng `LINK_TYPE_CONFLICT`; không chọn bằng số phiếu.
6. Không tạo link mới bằng cách thấy endpoint A ở window 1 và endpoint B ở window 2. Link phải tồn tại từ ít nhất một window có đủ bằng chứng hoặc từ một lượt gán nhãn bổ sung có cả hai đầu.

Vắng link không được dùng làm negative tự động: việc một window thấy hai endpoints chưa chứng minh nó thấy marker quan hệ hay đã kiểm duyệt mọi quan hệ. Đánh giá agreement LINKS chỉ so trên một scope đủ endpoints và evidence được quy định trước, như pilot ở 07.

### Bước F — Kiểm tra coverage theo từng task

Đừng dùng `min(start)` và `max(end)` để kết luận phủ đủ: giữa chúng có thể có lỗ hổng. Với mỗi task và element/view, lấy hợp các khoảng `[start,end)` của target segments từ các windows thực sự bật task đó. Hai khoảng tiếp giáp được nối trong coverage, không đồng nghĩa nối annotations.

Tính hai loại:

- `supplied`: nội dung đã được gửi, kể cả windows `ABSTAIN`.
- `reviewable`: nội dung đã gán nhãn, không dùng windows `ABSTAIN` hoặc có issue khác `TRUNCATED_TARGET`. Bản tham chiếu loại cả window có issue ngữ nghĩa để tránh tính dư vùng đã duyệt; production có thể trừ chính xác vùng issue sau khi có policy kiểm thử.

Đối chiếu với **toàn bộ** `[0,len(full_text))` của từng element/view trong target. Nếu chưa phủ, ghi chính xác gaps. Context không được tính vào coverage. Task `ROLES` chưa được window nào bật vẫn thiếu coverage dù `MENTIONS` đã phủ toàn target.

`TRUNCATED_TARGET` của cửa sổ chỉ được xem là vấn đề cơ học đã xử lý ở cấp merge khi coverage reviewable của các tasks đã đủ; vẫn giữ issue gốc trong provenance. Issues về OCR, ambiguity, missing context/modality, output limit hoặc input sai không được xóa chỉ vì windows chồng lên nhau.

### Bước G — Trả kết quả dẫn xuất

Artifact dẫn xuất gồm annotations không xung đột, links đã remap, local mapping, candidates/links bị cách ly, coverage và input issues. Trạng thái merge:

- `NEEDS_REVIEW`: còn xung đột hoặc issue phi cơ học.
- `INCOMPLETE`: chưa đủ coverage và không có nguyên nhân review ưu tiên hơn.
- `READY_FOR_REVIEW`: phủ đủ, không còn xung đột đã phát hiện; **không phải COMPLETE/gold/ACCEPTED**.

Hợp các windows chỉ chứng minh độ phủ text và tính nhất quán máy kiểm tra được. Nó chưa chứng minh không bỏ một định nghĩa dài vắt qua biên, chưa chứng minh nhãn đúng và không thay adjudication. Planner nên giữ nguyên câu/khối vai trò, hoặc cấp lại window đủ dài để nhìn toàn role trước khi người/quy trình duyệt chấp nhận.

## 4. Pseudocode

```text
validate_batch_identity_and_source()
for each window:
    validate_input_output()
    validate_segments_and_evidence_against_canonical_text()
    collect_exact_annotation_groups_and_origins()

detect_label_boundary_and_presence_conflicts()
quarantine_conflicted_groups()
build_mapping_from_item_and_local_id()

for each original link:
    resolve_both_endpoints_with_window_identity()
    if an endpoint is quarantined: retain_as_pending()
    else: deduplicate_link_by_type_and_merged_endpoints_keep_witnesses()
quarantine_conflicting_link_types()

compute_interval_union_and_gaps_per_task_element_view()
retain_all_original_issues_and_versions()
return_derived_artifact_with_review_status()
```

## 5. Các ca cần xác minh trước khi triển khai production

- Hai windows đều có `m001` nhưng trỏ tới hai occurrences khác nhau: không va chạm ID.
- Một occurrence nằm trong overlap của hai windows: dedup đúng một, provenance có hai origins.
- Cùng chữ ở hai vị trí: giữ đủ hai, kể cả cả hai cùng xuất hiện trong nhiều windows.
- Một link ở window thứ hai trỏ tới local ID khác window thứ nhất: remap đến cùng source endpoints.
- Nhãn hoặc ranh giới xung đột: cả candidates và links phụ thuộc bị cách ly.
- Windows mất một khoảng giữa text: coverage phải thấy lỗ hổng.
- Cửa sổ chỉ có context chứa phần thiếu: không được tính là target coverage.
- Thiếu một task trong toàn batch: không được báo sẵn sàng cho tất cả tasks.
- Views/revisions/hash khác nhau, malformed output, duplicate item ID: chặn merge.
- Đảo thứ tự windows đầu vào: kết quả IDs, annotations và mapping vẫn giống nhau.

Các ca này là kiểm thử thuật toán tham chiếu, không chứng nhận runtime Python đã có cơ chế merge tương ứng.
