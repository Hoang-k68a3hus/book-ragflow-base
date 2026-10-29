# 07 — Kiểm duyệt dữ liệu, chuẩn bị train và tích hợp runtime

Hợp đồng: `edu-annotation-1.1-pilot`. Phần này dành cho người/chương trình điều phối, không phải chỉ dẫn để LLM tự xác nhận chất lượng của mình.

## 1. Tách annotations và thông tin quản trị

Output LLM chỉ có các trường ở 04. Dataset record bên ngoài cần lưu thêm:

- Input gốc và hash; identity/revision của tài liệu, adapter/normalizer version, text view.
- Guideline version và hash; ontology version; prompt version/hash; few-shot IDs.
- Provider/model/revision và tham số chạy; số lần retry và lỗi validator.
- Output gốc, output sau sửa/duyệt, diff, lý do sửa ngắn và người/quy trình duyệt.
- `review_status`: `DRAFT`, `NEEDS_REVIEW`, `ACCEPTED`, `REJECTED` do workflow gán.
- Nguồn nhãn: `LLM_DRAFT`, `HUMAN_REVIEWED` hoặc trạng thái thực tế tương ứng. Hai LLM đồng ý không tự thành `HUMAN_REVIEWED`.
- Dataset version, split và nhóm chống rò rỉ dữ liệu; quyền sử dụng nguồn do đơn vị quản lý dữ liệu ghi nhận.

`COMPLETE` trong output không được dùng thay `ACCEPTED`. `ACCEPTED` cũng cần gắn với phiên bản ontology; nhãn chấp nhận theo guideline cũ có thể cần xét lại khi đổi ranh giới.

## 2. Luồng kiểm tra bắt buộc

```text
CanonicalDocument + phạm vi lấy mẫu
  → input validator
  → LLM gán nhãn
  → JSON/schema validator
  → kiểm tra evidence/mapping/overlap/links
  → kiểm duyệt đủ nhãn và đúng nghĩa
  → adjudication các ca bất đồng
  → dataset có version + manifest
```

### 2.1. Máy kiểm tra được gì

Kiểm tra hình dạng, enums, khóa lạ, IDs, membership, source view, quotes/offsets, phạm vi target, không overlap, thứ tự, trạng thái và endpoint links. Đối chiếu lại canonical document gốc trước khi nhập annotations.

Đối với chữ có dấu, phải kiểm tra trên cùng chuỗi đã dùng lúc gán nhãn; đừng normalize lần nữa trong validator. Mỗi lỗi cần đường dẫn trường và mã lỗi cụ thể. Không tự tìm và thay toàn bộ text dựa trên quote.

### 2.2. Máy không tự chứng minh được gì

JSON hợp lệ không chứng minh nhãn đúng nghĩa; evidence khớp không chứng minh chọn đúng loại; coverage nhiều annotations không chứng minh đủ mọi thực thể. Cần kiểm duyệt false positives, false negatives, ranh giới và lỗi theo môn.

Một reviewer phải đọc lại nguồn, không chỉ nhìn danh sách candidate. Nếu hai người/model bất đồng, dùng guideline và bằng chứng để adjudicate; không mặc định đa số thắng. Ca chưa giải quyết giữ issue và không chuyển thành âm tính.

### 2.3. Rubric kiểm duyệt

| Nhóm lỗi | Ví dụ | Xử lý |
|---|---|---|
| Nguồn/evidence | Tự sửa OCR, quote không khớp, evidence ngoài target | Chặn nhập; sửa mapping hoặc gán lại |
| Nhãn | PERSON trong tên định lý, EXERCISE thành PROOF | Sửa nhãn theo guideline; ghi nguyên nhân |
| Ranh giới | Mất phủ định/giả thiết, span chứa danh xưng thừa | Sửa và kiểm tra offset lại |
| Thiếu nhãn | Chỉ gán occurrence đầu, bỏ thuật ngữ tiếng Anh | Gán bổ sung và rà soát trường hợp cùng kiểu |
| Roles | Tạo role từ heading trống, roles lồng không hợp lệ | Chia lại vùng chức năng hoặc loại nhãn |
| Links | Tự suy alias, endpoint ở context | Loại link; không tạo node giả |
| Trạng thái | Partial thành complete/âm tính | Sửa trạng thái và loại vùng thiếu khỏi loss |

## 3. Kế hoạch xây data

### 3.1. Lấy mẫu đại diện

Chia theo môn, lớp 6–12, nguồn sách/tài liệu, loại trang, ngôn ngữ, chất lượng OCR và kiểu content. Có cả heading, bảng, chú thích, bài tập, lời giải, phần lý thuyết và mẫu âm tính. Không chỉ chọn đoạn sạch đã có nhiều tên riêng.

Pilot 300–500 đoạn dùng để sửa guideline và đo độ thống nhất. Sau đó có thể tăng lên 2.000–3.000 đoạn đã duyệt cho baseline, rồi bổ sung dựa trên lỗi và đường học. Đây là mốc triển khai đề xuất, không phải điều kiện bảo đảm đạt một mức F1.

Theo dõi số occurrences, số tài liệu độc lập và đa dạng cách diễn đạt theo nhãn. Một nhãn có hàng nghìn occurrences lặp trong cùng một sách không tương đương nhiều ngữ cảnh độc lập.

### 3.2. Chất lượng người duyệt

Lấy một phần train để hai người gán độc lập, ưu tiên nhãn khó và môn chuyên biệt. Validation/test cần kiểm duyệt kỹ và adjudication bất đồng. Ghi mức thống nhất span+label theo từng nhãn; không chỉ đo tỷ lệ đồng ý trên token O vì phần lớn text có thể không mang nhãn.

LLM tạo bản nháp giúp giảm thao tác nhưng phải kiểm tra cả omission. Một model thường xuyên bỏ cùng loại thuật ngữ có thể khiến reviewer chỉ sửa nhãn có sẵn mà không nhận ra thiếu nhãn.

### 3.3. Chia tập trước khi chia cửa sổ

- Nhóm theo sách, bản tái bản, bản scan/digital, bản dịch và tài liệu dẫn lại cùng nội dung.
- Kiểm tra exact/near duplicates trước khi chia; giữ các phiên bản hoặc đoạn gần trùng ở cùng split.
- Chia theo nhóm nguồn, rồi mới tạo windows. Không random split các đoạn/cửa sổ của cùng sách.
- Train/validation/test có thể bắt đầu ở tỷ lệ 80/10/10 theo nhóm, điều chỉnh để mỗi nhãn có đủ ca đánh giá.
- Có một lát cắt riêng cho tài liệu ngoài nguồn train, tiếng Anh, Việt–Anh, OCR xấu và nhãn hiếm.
- Các bản dịch song song không được chia một ngôn ngữ vào train và ngôn ngữ kia vào test nếu chúng cùng nội dung.

### 3.4. Protocol pilot 300–500 đoạn trước khi mở rộng

Đây là kế hoạch thực hiện, **chưa phải kết quả đã đo**. Chọn một tập pilot có source IDs cố định, nhiều môn/lớp và cả mẫu âm tính; tách phần dùng hiệu chỉnh guideline khỏi phần kiểm tra lại sau hiệu chỉnh. Không chọn chỉ các câu có marker “Định nghĩa/Ví dụ”.

| Bước | Thực hiện | Artifact phải lưu |
|---|---|---|
| 1. Đóng băng lượt pilot | Chốt guideline/hash, prompt, source revision, tasks, cách chia windows, ngưỡng release dự kiến theo nhãn | Manifest pilot và danh sách item IDs |
| 2. Hai lượt độc lập | A và B nhận cùng input và cùng phiên bản; không được thấy output/nhận xét của lượt kia | Hai bản annotation nguyên gốc, model/run IDs và trạng thái validator |
| 3. So sánh trước adjudication | Đo exact span + label, lỗi thiếu/thừa, nhãn, boundary, role priority và links | Báo cáo agreement theo môn/nhãn/ngôn ngữ; tỷ lệ vùng bị loại vì issue |
| 4. Adjudication | Người/quy trình duyệt có hiểu biết bộ môn quyết định bằng quy tắc và nguồn; trường hợp guideline còn hở phải sửa guideline | Quyết định, mã nguyên nhân và cặp ví dụ tối thiểu mới |
| 5. Gán lại phần bị ảnh hưởng | Chạy lại cả A/B với guideline mới; giữ version cũ để so sánh công bằng | Báo cáo lượt mới; không sửa nhãn cũ rồi giả là agreement ban đầu |
| 6. Quyết định mở rộng | Đối chiếu tiêu chí đã đăng ký và các nhóm nhãn hiếm; xác nhận validator/merge/source checks | Quyết định release dataset và phạm vi còn chưa đủ dữ liệu |

Hai lượt LLM độc lập là tín hiệu ổn định, không tự thay thế gold chuyên môn; cùng model có thể lặp cùng lỗi. Khi có điều kiện, dùng người duyệt cho phần khó và một tập kiểm tra chuyên môn tách biệt. Không dựng số agreement khi chưa có nguồn thật và kết quả A/B.

**Định nghĩa phép đo bắt buộc:**

- Mentions: set key `(element_id, text_view, start, end, label)` sau khi validate quote. Gọi `A`, `B` là hai tập trên cùng vùng đã kiểm tra. Exact agreement F1 đối xứng là `2 * |A ∩ B| / (|A| + |B|)`. Một thay đổi boundary hoặc label là một bất đồng, không fuzzy-match rồi báo thành exact match.
- Roles: key gồm label và toàn bộ ordered evidence ranges. Báo exact agreement riêng với overlap diagnostic; không dùng overlap score thay cho exact score.
- Links: chuyển local endpoints thành annotation source keys rồi so `(type, source_key, target_key)`, không so `m001/r001` giữa hai lượt. Báo riêng agreement khi endpoints đã thống nhất và end-to-end khi endpoints còn có thể bất đồng.
- Nếu cả A/B rỗng, mẫu đó là agreement âm tính nhưng F1 riêng mẫu là không xác định; báo số mẫu âm tính trùng nhau riêng. Không gán F1=1 cho hàng nghìn đoạn O để làm điểm trung bình đẹp hơn. Micro F1 tính trên tổng counts toàn phạm vi so sánh.
- `PARTIAL` chỉ được so trên giao các vùng chắc chắn đã kiểm tra; nếu issue không định vị được, loại item khỏi phép đo tương ứng và báo tỷ lệ bị loại. `ABSTAIN` không thành một tập âm tính. Không loại ca khó mà không báo exclusion/abstention rate.
- Mỗi bất đồng được phân vào `BOUNDARY`, `LABEL`, `PRESENCE`, `ROLE_PRIORITY`, `LINK_EVIDENCE`, `SOURCE_QUALITY` hoặc `GUIDELINE_GAP`. Phân tích nguyên nhân trước khi tăng dữ liệu train.

Các ngưỡng release phải được xác định trước khi xem kết quả của tập kiểm tra lại. Nếu một nhãn có quá ít mẫu độc lập để đánh giá, ghi rõ chưa đủ bằng chứng; không coi điểm gộp cao là đã qua cổng cho nhãn đó.

## 4. Xuất dữ liệu train

### 4.1. Mentions → BIO

Giữ character-span dataset làm nguồn chuẩn. Tạo tokens/subwords và BIO bằng chương trình có version tokenizer, không yêu cầu LLM tự token hóa để làm nguồn chuẩn.

- `B-X`: token bắt đầu occurrence nhãn X; `I-X`: phần tiếp theo; `O`: vùng đã được xác nhận không có nhãn.
- Chọn và ghi rõ chính sách subword: chỉ tính loss trên subword đầu hoặc gán cho toàn bộ subword theo quy tắc nhất quán. Special tokens/padding luôn bị mask.
- Cửa sổ được cắt theo tokenizer của model, có overlap và ánh xạ về character offsets. Điều chỉnh biên để tránh cắt mentions đã biết nếu có thể.
- Nếu mention nằm vắt biên, window không chứa đủ mention không được coi đoạn đó là O. Mask vùng biên hoặc bỏ window tương ứng theo policy đã ghi.
- Mỗi occurrence phải có ít nhất một cửa sổ nhìn thấy đầy đủ trước khi coi target đã được train đầy đủ.
- `PARTIAL`/`ABSTAIN` không được chuyển toàn text chưa gán thành O. Hoặc loại item, hoặc mask chính xác vùng chưa duyệt; issue không có phạm vi xác định thường buộc loại toàn item khỏi supervised loss.
- `CONCEPT`, `METHOD`, `NAMED_PRINCIPLE` cùng nằm trong lớp phẳng đã quy định, không sinh BIO lồng nhau.

Không tạo nhãn cho phần target bị request builder bỏ. Coverage của cửa sổ phải được theo dõi ngoài output LLM và hợp nhất với scope nguồn.

Ngoại lệ cần phân biệt với bất định ngữ nghĩa: một cửa sổ `PARTIAL` chỉ vì `TRUNCATED_TARGET` có thể có toàn bộ phần nhìn thấy đã được duyệt. Chương trình được dùng vùng đã duyệt đó cho loss sau khi xác định chính xác scope và xử lý mentions vắt biên; tuyệt đối không tạo O cho phần ngoài cửa sổ. Chỉ đổi trạng thái hoàn tất ở cấp target khi bộ hợp nhất đã kiểm tra phủ đủ các cửa sổ, giải quyết mọi xung đột và không còn vùng chưa duyệt. Không xóa issue của output LLM gốc; lưu kết quả hợp nhất như bản ghi dẫn xuất có provenance.

### 4.2. Roles và links

Roles là bài toán phân đoạn/phân loại chức năng, không nhập chung vào BIO mentions. Có thể tạo các mẫu role spans hoặc logical-unit classification riêng. LogicalUnit chứa nhiều roles phải được chia vùng hoặc dùng biểu diễn đa đoạn theo evidence; không ép một nhãn lên cả unit nếu guideline đã tách nhiều vùng.

Links tạo tập endpoint pairs riêng. Cặp không có link trong output chưa chắc là negative nếu tasks không bật, target thiếu hoặc quan hệ nằm ngoài ontology. Chỉ tạo negative pairs theo phạm vi đã kiểm duyệt.

## 5. Mapping sang SemanticProvider hiện tại

Code liên quan:

- [provider.py](../semantics/provider.py): `SemanticRequest`, `SemanticCandidate`, capabilities và ontology.
- [annotator.py](../semantics/annotator.py): chọn candidate, dedup, giới hạn target, lưu annotation.
- [request.py](../semantics/request.py): dựng request, context và truncation.
- [document.py](../schemas/document.py): annotation types, evidence, payload modes.
- [pipeline_support.py](../pipeline_support.py): ranh giới bất biến giữa cấu trúc và semantic.
- [retrieval builder](../retrieval_units/builder.py): tạo đơn vị retrieval.

### 5.1. Mentions

| Nhãn dataset | `SemanticAnnotationType` | Ontology namespace:label |
|---|---|---|
| `CONCEPT` | `CONCEPT` | `edu:CONCEPT` |
| `NAMED_PRINCIPLE` | `CONCEPT` | `edu:NAMED_PRINCIPLE` |
| `METHOD` | `CONCEPT` | `edu:METHOD` |
| `PERSON` | `ENTITY` | `ner:PERSON` |
| `LOCATION` | `ENTITY` | `ner:LOCATION` |
| `ORGANIZATION` | `ENTITY` | `ner:ORGANIZATION` |
| `WORK` | `ENTITY` | `ner:WORK` |
| `EVENT` | `ENTITY` | `ner:EVENT` |
| `PRODUCT` | `ENTITY` | `ner:PRODUCT` |

Đây là mapping thống nhất cho provider sẽ xây, không phải registry đã được runtime đăng ký sẵn. `value=surface`, `payload_mode=EXTRACTIVE`, evidence sao chép sau khi validate. `source=INFERRED` cho output model. Ontology version do provider ghi là `edu-annotation-1.1-pilot`.

Không ép named principle/method thành `THEOREM`/`PROCEDURE`: tên và vai trò đoạn khác nhau. Namespace `edu` và `ner` phải được khai báo trong capability, cùng annotation types và target kinds thực sự hỗ trợ.

### 5.2. Roles

Các nhãn roles V1 có tên tương ứng trong `SemanticAnnotationType`. Mapping dùng type cùng tên, `payload_mode=LABEL_ONLY`, ontology `edu:<ROLE_LABEL>`, `value=<ROLE_LABEL>` là nhãn ổn định, không phải câu do model viết. Evidence giữ những source spans định nghĩa phạm vi chức năng.

Không ghi tóm tắt, lời giải hay văn bản sinh vào `value` dưới `LABEL_ONLY`. V1 không sinh `SUMMARY`, `KEY_POINT`, `LEARNING_OBJECTIVE`; các payload generative cần hợp đồng đánh giá độ trung thực riêng.

### 5.3. Confidence và identity

Dataset output không chứa confidence tự ước lượng. Provider huấn luyện sau này có thể đưa điểm model vào candidate với `confidence_method=EMPIRICAL_PROVIDER_SCORE` hoặc `UNCALIBRATED` theo cách tính thực tế. Chỉ dùng `CALIBRATED_PROBABILITY` khi đã hiệu chuẩn trên dữ liệu tách biệt và lưu `calibration_version`.

Không gán confidence 1.0 cho nhãn LLM chỉ vì JSON hợp lệ. Nếu provider dựa trên luật, ý nghĩa `RULE_PRIOR` cũng phải được ghi rõ. Chọn ngưỡng theo loại nhãn bằng validation; không đánh đồng điểm token cao với xác suất toàn entity đúng.

ID dataset `m001/r001` không phải canonical annotation ID. Bộ chuyển đổi phải dùng cơ chế ID/version của annotator và lưu mapping item/local ID → annotation ID bên ngoài. Không để ID phụ thuộc duy nhất vào surface khi có occurrences lặp.

### 5.4. Links chưa có đường nhập tương ứng

Các link dataset `ALIAS_OF`, `TRANSLATION_OF`, `ANSWER_TO`, `EXAMPLE_OF`, `PROOF_OF` không phải enum relations trực tiếp tương ứng trong code hiện tại. `SemanticProvider.annotate` chỉ trả candidates, không có contract trả relation edges; semantic stage cũng bị cấm sửa `document.relations`.

Vì vậy V1 lưu links trong dataset/artifact phụ có version để train/đánh giá riêng. Không lách bằng cách gắn links vào structural relations hoặc biến thành `CUSTOM` annotation không có thiết kế. Muốn đưa vào canonical graph cần một thay đổi schema/stage được thiết kế và kiểm thử riêng.

## 6. Các giới hạn runtime phải xử lý trước khi nhập nhãn đầy đủ

1. `SemanticAnnotationPolicy.max_annotations_per_target` mặc định 8, giới hạn cấu hình hiện tại tối đa 64. Dataset không được cắt về 8; runtime cần policy phù hợp và báo cáo overflow thay vì âm thầm mất mentions.
2. Annotator hiện dedup theo type + ontology + value, có thể bỏ occurrences cùng chữ khác offset. Cần dedup theo evidence identity khi mục tiêu là occurrence-level; IDs có evidence không tự giải quyết được bước dedup này.
3. Request builder hiện có thể truncate/bỏ target elements. Chia cửa sổ phải diễn ra trước khi mất text; provider không khôi phục được phần chưa nhận.
4. Annotator hiện tra request theo `target_id`; gửi nhiều windows cùng target vào một lượt mà không sửa orchestration có thể đè mapping. Cần contract window ID/hợp nhất riêng hoặc xử lý từng cửa sổ rồi hợp nhất có kiểm tra trước khi ghi kết quả.
5. Nên nhận diện mentions trên elements và roles trên logical units. Nếu cùng provider chạy cả hai target kinds, phải tránh đếm/gán một occurrence hai lần ở element và unit.
6. Retrieval builder hiện đặt `semantic_enrichment_used=False` và `semantic_annotations=()`. Thêm provider chưa làm retrieval dùng nhãn; cần bổ sung projection và kiểm tra evidence thuộc đúng retrieval unit, đặc biệt khi unit bị chia theo token.
7. Semantic enrichment phải giữ nguyên source content, location, cấu trúc, quality và relations theo boundary hiện tại. Phần source bị lỗi phải đi qua quy trình parse/normalize có version, không do NER âm thầm sửa.

Các điểm này đã được đối chiếu trực tiếp với source trong lượt cập nhật guideline: policy và dedup ở `semantics/annotator.py`; truncation ở `semantics/request.py`; request lookup theo target ở annotator; cờ/annotations retrieval ở `retrieval_units/builder.py`; semantic boundary ở `pipeline_support.py`. Đây là xác nhận qua đọc code, chưa là kiểm thử end-to-end có model và backend thật.

Thuật toán dataset merge và bản tham chiếu độc lập nằm ở [08](08_window_merge_vi.md). Chúng chưa được gọi bởi pipeline Python. Các rủi ro runtime ở trên vẫn là yêu cầu triển khai tiếp theo; kiểm thử bản tham chiếu không chứng minh runtime đã xử lý chúng.

## 7. Đánh giá và phát hành dataset/model

| Lớp đánh giá | Chỉ số/cách kiểm |
|---|---|
| Mentions | Exact-span + label precision/recall/F1, micro và từng nhãn; phân tích lỗi boundary riêng |
| Roles | Đúng nhãn và phạm vi evidence; báo cáo exact match và overlap như hai chỉ số khác nhau |
| Links | Đúng endpoints + type; báo cáo cả với endpoints gold và với endpoints dự đoán |
| Chất lượng nguồn | Lát cắt OCR sạch/xấu, bảng, header/TOC, target bị cắt |
| Ngôn ngữ | Việt, Anh, trộn Việt–Anh riêng; không chỉ điểm gộp |
| Bộ môn/lớp | Theo nhóm môn và cấp THCS/THPT; lớp chưa có nguồn xác nhận giữ unknown |
| Vận hành | Quote/offset validity, truncation, số nhãn bị loại, latency và bộ nhớ |
| Retrieval | Recall@k/MRR/nDCG trên câu hỏi có đoạn nguồn được duyệt; câu hỏi không cần NER cũng phải được đo |

Role/mention coverage và mean confidence trong runtime là diagnostics, không phải accuracy. Không dùng chúng làm tiêu chí thay thế tập gold.

Trước phát hành: khóa test set; duyệt các issues còn lại; ghi phân bố nhãn/nguồn/ngôn ngữ; kiểm tra leakage và version; xác định tiêu chí đạt trên validation trước khi nhìn test. Không đặt một ngưỡng F1 chung rồi bỏ qua nhãn hiếm hoặc câu hỏi retrieval bị giảm chất lượng.

## 8. Vòng lặp bổ sung dữ liệu

Chọn mẫu theo lỗi thực tế: nhầm named principle/person, bỏ thuật ngữ Việt–Anh, ranh giới qua xuống dòng, bài tập/đáp án, mentions lặp, OCR và bảng. Trộn mẫu khó với mẫu lấy ngẫu nhiên để không làm dữ liệu lệch hoàn toàn sang ca khó.

Chỉ dùng predictions trên train pool để chọn mẫu. Không sửa gold test theo model để cải thiện điểm. Khi guideline thay đổi thật sự, phát hành dataset version mới và công bố sự khác biệt thay vì so trực tiếp hai điểm không cùng điều kiện.
