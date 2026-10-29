# 04 — Hợp đồng input/output và offset

Phiên bản: `edu-annotation-1.1-pilot`. JSON Schema: [input](schemas/input.schema.json), [output](schemas/output.schema.json). Các schema kiểm tra hình dạng; các bất biến về text, offset, endpoint và độ đầy đủ phải được kiểm tra bổ sung bằng chương trình/kiểm duyệt.

## 1. Một item, một target

Mỗi lượt LLM nhận một item và trả một output. `item_id` định danh lần chọn mẫu/cửa sổ; `request.target_id` định danh Element hoặc LogicalUnit. Không trộn nhiều target vào một output. Nếu một target được chia thành nhiều cửa sổ, mỗi cửa sổ có `item_id` riêng và cùng target ID; chương trình phải hợp nhất bằng source spans sau đó.

Root input có các trường bắt buộc:

| Trường | Nghĩa và quy tắc |
|---|---|
| `contract_version` | Chính xác `edu-annotation-1.1-pilot` |
| `item_id` | ID có sẵn, LLM sao chép nguyên vẹn |
| `tasks` | Danh sách duy nhất trong `MENTIONS`, `ROLES`, `LINKS`; không rỗng; `LINKS` không chạy một mình |
| `target_coverage` | `COMPLETE` nếu toàn bộ nội dung target đã được cung cấp, `PARTIAL` nếu bị cắt/bỏ/thiếu |
| `source_metadata` | Metadata do hệ thống cung cấp, không để LLM điền lại |
| `request` | Cấu trúc tương ứng `SemanticRequest` của runtime |
| `element_hints` | Danh sách gợi ý cấu trúc đã biết; có thể rỗng |

`source_metadata` gồm `document_id`, `content_hash`, `source_revision`, `source_name`, `subject`, `grade`, `language`. Các giá trị chưa biết là `null`; `grade` chỉ là số 6–12 hoặc null. Không gán một lớp duy nhất cho tài liệu nhiều lớp; để null và ghi chi tiết ở manifest bên ngoài. `content_hash`, khi có, phải là hash nguồn do hệ thống xác định, không phải hash do LLM tạo.

Mỗi `element_hints` có `element_id`, `element_type` (string hoặc null), `source_zone` trong `body`, `header`, `footer`, `toc`, `bibliography`, `caption`, `unknown`. Nó không bổ sung text hay evidence. Không tự suy `source_zone` rồi sửa input.

## 2. Request và segment

`request` có các trường:

- `target_id`, `target_kind` (`ELEMENT` hoặc `LOGICAL_UNIT`).
- `text`: chuỗi tổng được đưa vào model, có thể gồm cả target và context.
- `language`: ngôn ngữ đã biết hoặc null; không phải nhãn để LLM dự đoán.
- `element_ids`: các element thuộc target, không lặp.
- `target_segments`: lát cắt nguồn được phép gán nhãn.
- `context_segments`: lát cắt chỉ hỗ trợ suy luận.
- `logical_unit_type`, `unit_label`, `context_labels`: thông tin ngữ cảnh nguồn.
- `metadata`: JSON metadata của request, bao gồm thông tin truncation khi có.

Mỗi segment có:

```json
{
  "element_id": "el_001",
  "text": "FPT ở Hà Nội.",
  "text_view": "RAW_TEXT",
  "element_start": 0,
  "element_end": 13,
  "request_start": 0,
  "request_end": 13
}
```

`text_view` là `RAW_TEXT` hoặc `NORMALIZED_TEXT`. Gán trên đúng view đã được hệ thống chọn. Không tự đổi view giữa chừng; không dùng offset của raw text cho normalized text.

Bất biến input:

1. `request.text[request_start:request_end] == segment.text` theo cách đếm code point.
2. `element_end - element_start == len(segment.text)`.
3. `request_end - request_start == len(segment.text)`.
4. Các segments không chồng lấn trong request. Khoảng trống/separator giữa chúng không thuộc element nào, không được gán nhãn.
5. Target segments chỉ tham chiếu `element_ids`; một target segment cho mỗi element trong một request, theo contract runtime.
6. Target `ELEMENT` có đúng một element và một target segment khớp ID đó.
7. Nếu `target_kind=ELEMENT`, target ID bằng ID element; nếu `LOGICAL_UNIT`, hệ thống phải kiểm tra target và membership với canonical document gốc.

## 3. Offset: code point, không phải byte hay token

Offset bắt đầu từ 0, đầu bao gồm/cuối không bao gồm (`[start_char, end_char)`). Đếm **Unicode code points** trên đúng chuỗi đã giải mã JSON, tương ứng `len` và slice chuỗi Python. Không đếm UTF-8 bytes, token, grapheme hiển thị hoặc UTF-16 code units.

Ví dụ `FPT ở Hà Nội.`: `FPT` có khoảng `[0,3)`, `Hà Nội` có `[6,12)`. Ký tự `\n` sau khi parse JSON là một code point; CRLF là hai. Escape JSON `\u00e0` sau giải mã là một ký tự. Chữ NFD có dấu tổ hợp có thể chiếm nhiều code points dù nhìn như một chữ. Emoji ngoài BMP có thể chiếm một code point nhưng hai UTF-16 code units.

Trong JavaScript, dùng `Array.from(text).slice(start,end).join("")` để đối chiếu; không dùng trực tiếp `text.slice` với offset code point. Hệ thống phải chuyển offset từ công cụ gán nhãn nếu công cụ dùng UTF-16.

Nếu ứng viên nằm ở khoảng local `[a,b)` trong `segment.text`:

```text
start_char = segment.element_start + a
end_char   = segment.element_start + b
quoted_text = segment.text[a:b]
```

Nếu model trả khoảng `[p,q)` trong request, cần tìm đúng segment chứa toàn khoảng rồi đổi:

```text
start_char = segment.element_start + p - segment.request_start
end_char   = segment.element_start + q - segment.request_start
```

LLM phải kiểm tra quote/offset. Khi không tính được đáng tin, ghi `UNCERTAIN_BOUNDARY` và không phát hành annotation đó. Chương trình không được sửa bằng cách tìm occurrence đầu tiên của quote; quote có thể lặp. Có thể tính lại offset chỉ khi vị trí trong segment và lần xuất hiện đã được xác định duy nhất, đồng thời ghi lại thao tác sửa.

## 4. Output bắt buộc

Output có đúng các trường sau, không thêm metadata hay prose:

```json
{
  "contract_version": "edu-annotation-1.1-pilot",
  "item_id": "sample_001",
  "target_id": "el_001",
  "status": "COMPLETE",
  "mentions": [],
  "roles": [],
  "links": [],
  "issues": []
}
```

`mentions`, `roles`, `links`, `issues` luôn tồn tại, kể cả khi rỗng. Schema không cho phép khóa bổ sung.

### 4.1. Mention

```json
{
  "id": "m001",
  "label": "LOCATION",
  "surface": "Hà Nội",
  "evidence": {
    "element_id": "el_001",
    "text_view": "RAW_TEXT",
    "start_char": 6,
    "end_char": 12,
    "quoted_text": "Hà Nội"
  }
}
```

Evidence phải nằm trọn trong một target segment. `surface == quoted_text` chính xác. Hai mentions cùng surface ở hai vị trí là hai bản ghi khác nhau. Không thêm `canonical_name`, bản dịch, entity ID, confidence hoặc mô tả.

### 4.2. Role

```json
{
  "id": "r001",
  "label": "EXERCISE",
  "evidence": [
    {
      "element_id": "el_002",
      "text_view": "RAW_TEXT",
      "start_char": 0,
      "end_char": 11,
      "quoted_text": "Tính 2 + 3."
    }
  ]
}
```

Một hoặc nhiều evidence spans theo thứ tự nguồn; không rỗng. Các spans phải tạo một vùng chức năng liên tiếp theo quy tắc ở 02. Không tạo trường `value` chứa lời diễn giải hoặc câu trả lời. Khi chuyển sang runtime, bộ chuyển đổi mới tạo giá trị nhãn theo mapping ở 07.

### 4.3. Link

Link có `id`, `type`, `source_id`, `target_id`, `evidence` (mảng không rỗng). `source_id`/`target_id` ở đây là **ID annotation cục bộ**, khác với target ID của request. Endpoint phải có trong cùng output và đúng loại quy định ở 02. Evidence link có thể chồng với evidence annotation; phải chứng minh quan hệ, không chỉ chép hai tên không có tín hiệu liên kết.

### 4.4. Issue

Issue có `id`, `code`, `evidence` và `note`. `note` là một câu ngắn bằng tiếng Việt (tối đa 500 ký tự), chỉ mô tả điều quan sát được cần xử lý. Không chứa đề xuất nhãn đoán mò hay lời giải bài tập.

| Code | Khi dùng |
|---|---|
| `AMBIGUOUS_LABEL` | Không phân biệt được loại mention/nghĩa trong ngữ cảnh |
| `AMBIGUOUS_ROLE` | Không phân biệt được chức năng đoạn sau khi áp dụng ưu tiên |
| `UNCERTAIN_BOUNDARY` | Không xác định được ranh giới hoặc offset chính xác |
| `OCR_UNREADABLE` | Lỗi nhận dạng làm không xác định đáng tin biểu thức/chức năng |
| `TRUNCATED_TARGET` | Target bị cắt/bỏ một phần; không được xem vùng thiếu là âm tính |
| `CROSS_SEGMENT_MENTION` | Một tên/thuật ngữ bị tách qua elements, không thể biểu diễn bằng mention liên tục |
| `MISSING_CONTEXT` | Thiếu ngữ cảnh thiết yếu để quyết định nhãn, không phải chỉ thiếu context tùy chọn |
| `MISSING_MODALITY` | Thiếu hình/công thức/bố cục cần thiết mà text không đủ |
| `INVALID_INPUT` | ID/range/contract hỏng hoặc input tự mâu thuẫn |
| `OUTPUT_LIMIT` | Không thể xuất đủ nhãn trong giới hạn phản hồi; phải chia lại item |

Evidence issue thường là đoạn bị ảnh hưởng. Chỉ cho phép `[]` khi không có span xác định được, như phần target hoàn toàn bị bỏ, mapping sai hoặc giới hạn output. Không tạo span giả để chỉ vị trí không tồn tại. Issue không phải một nhãn huấn luyện dương tính.

## 5. ID và thứ tự

- Mentions: `m001`, `m002`, …; roles: `r001`, …; links: `l001`, …; issues: `i001`, … . Sau 999 tiếp tục 1000, không reset.
- IDs chỉ có nghĩa trong một item; không dùng chúng làm ID toàn tài liệu.
- Khi hợp nhất, lookup bằng tuple `(item_id, local_id)`, dedup bằng evidence gốc rồi mới remap links. Không nối mảng JSON trực tiếp. Thuật toán và artifact dẫn xuất được quy định ở [08](08_window_merge_vi.md).
- Mentions/roles được sắp theo thứ tự segment trong request rồi theo offset của evidence đầu; evidence của mỗi role/link/issue cũng theo thứ tự nguồn.
- Cùng điểm bắt đầu, xếp khoảng kết thúc tăng dần, rồi nhãn theo alphabet nếu cần tie-break. Annotation trùng hoàn toàn phải được loại bỏ.
- Links xếp theo `type`, `source_id`, `target_id` theo thứ tự chuỗi; sau đó mới đánh `l001…`.
- Issues có evidence xếp theo vị trí đầu; issues không có evidence xếp cuối, theo code. Sau đó đánh ID.
- Không gộp hai occurrences khác offset thành một mention. Không khử trùng lặp bằng surface đơn thuần.

## 6. COMPLETE, PARTIAL và ABSTAIN

| Trạng thái | Điều kiện bắt buộc |
|---|---|
| `COMPLETE` | Toàn target được cung cấp; không còn issue; đã thực hiện hết nhiệm vụ bật. Có thể không có annotations |
| `PARTIAL` | Còn vùng chưa giải quyết, target thiếu hoặc output chưa đầy đủ; ít nhất một issue; có thể có hoặc không có annotations chắc chắn |
| `ABSTAIN` | Không thể xử lý target đáng tin; mọi mảng mentions/roles/links rỗng; ít nhất một issue |

Nếu chỉ một ứng viên mơ hồ còn text khác vẫn đọc được, dùng `PARTIAL`, kể cả chưa có annotation chắc chắn nào. `ABSTAIN` dành cho việc toàn bộ yêu cầu không xử lý được.

`target_coverage=PARTIAL` buộc `PARTIAL` hoặc `ABSTAIN` cùng `TRUNCATED_TARGET`. `request.metadata.truncated_element_ids` hoặc `omitted_target_element_ids` không rỗng cũng buộc như vậy. Nếu chỉ `omitted_context_element_ids` không rỗng, không tự bắt buộc partial: context bị bỏ có thể không cần thiết. Nếu chỉ có `request_truncated=true` mà không có phân loại phần bị bỏ, hệ thống phải xác định coverage trước khi gọi; nếu không xác định được, trả issue `INVALID_INPUT`.

Mỗi cửa sổ chỉ có một phần của target gốc mang `target_coverage=PARTIAL`. Kết quả cửa sổ có thể đúng trên mọi span nhìn thấy nhưng vẫn chưa hoàn tất target; bộ hợp nhất chịu trách nhiệm xác minh toàn bộ cửa sổ phủ đủ trước khi bỏ trạng thái thiếu ở cấp target.

## 7. Kiểm tra ngoài JSON Schema

Chương trình bắt buộc kiểm tra:

1. IDs/version đầu ra khớp input, nhiệm vụ tắt không có annotations.
2. Evidence khớp chính xác text và view của segment, không chỉ khớp một quote ở nơi khác.
3. Không evidence ngoài target hoặc nằm trong separator/context.
4. Không overlap trong lớp mentions; không overlap trong lớp roles; evidence nội bộ không trùng.
5. IDs liên tục, duy nhất, đúng thứ tự; endpoints links tồn tại và đúng loại/hướng.
6. Status và issues hợp lệ; target bị thiếu không mang `COMPLETE`.
7. Coverage của nhãn được kiểm duyệt, không suy từ JSON hợp lệ.
8. Trước khi ghi canonical annotation, đối chiếu lại với **canonical document gốc**, không chỉ tin bản request do client gửi.

Schema đầy đủ sử dụng JSON Schema Draft 2020-12. Nếu hệ thống structured output chỉ hỗ trợ một tập con, dùng schema giản lược cho sinh kết quả và vẫn chạy validator đầy đủ sau đó; không bỏ các bất biến vì API không hỗ trợ một keyword.

Script trong `examples/validate_examples.mjs` là trình kiểm tra fixture, chỉ thực thi các keyword thực sự dùng trong hai schema này và báo lỗi nếu gặp keyword lạ. Nó không phải bộ chứng nhận tuân thủ toàn bộ Draft 2020-12; production cần schema engine tiêu chuẩn cộng với các kiểm tra ngữ nghĩa ở trên.
