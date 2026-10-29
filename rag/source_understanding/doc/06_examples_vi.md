# 06 — Ví dụ chuẩn, phản ví dụ và cách quyết định

Hợp đồng: `edu-annotation-1.1-pilot`. [annotation_cases.jsonl](examples/annotation_cases.jsonl) chứa 68 ca **tự soạn để minh họa**, không phải tập gold đã được chuyên gia bộ môn duyệt và không phải benchmark đo model. Mỗi dòng có `case_id`, `title`, `input`, `expected`. Khi dùng few-shot, lấy object `input` làm user message và object `expected` làm assistant message; không gửi nguyên wrapper.

Các fixtures giữ đủ schema, text view, nhiệm vụ, offset và status. Danh sách dưới đây giải thích quyết định; JSONL là bản máy đọc. “Không gán” trong một task tắt chỉ có nghĩa không yêu cầu task đó, không chứng minh nguồn không có annotation loại đó.

## 1. Ví dụ đầy đủ đơn giản

Nguồn `FPT ở Hà Nội.`, `tasks=["MENTIONS"]`, target element `el_001`, text view `RAW_TEXT`, segment bắt đầu tại offset 0:

```json
{
  "contract_version": "edu-annotation-1.1-pilot",
  "item_id": "sample_001",
  "target_id": "el_001",
  "status": "COMPLETE",
  "mentions": [
    {
      "id": "m001",
      "label": "ORGANIZATION",
      "surface": "FPT",
      "evidence": {
        "element_id": "el_001",
        "text_view": "RAW_TEXT",
        "start_char": 0,
        "end_char": 3,
        "quoted_text": "FPT"
      }
    },
    {
      "id": "m002",
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
  ],
  "roles": [],
  "links": [],
  "issues": []
}
```

Sai nếu đổi surface thành “Thành phố Hà Nội”, lấy `[5,12)` gồm khoảng trắng, tạo link “FPT đặt trụ sở tại Hà Nội” hoặc thêm confidence tự đoán. Câu này cũng không được dùng để khẳng định quan hệ trụ sở: nguồn chỉ nói “ở”.

## 2. Tên, khái niệm, phương pháp

| Ca | Quyết định chuẩn | Lỗi cần tránh |
|---|---|---|
| c01 | `FPT/ORGANIZATION`, `Hà Nội/LOCATION` | Tự mở rộng tên hoặc suy quan hệ |
| c02 | `định lý Pythagore/NAMED_PRINCIPLE`, `tam giác vuông/CONCEPT` | Thêm `Pythagore/PERSON` chồng lấn; gán cả câu là định lý |
| c03 | `phương pháp thế/METHOD`, `GeoGebra/PRODUCT` | Gán “Dùng” là phương pháp hoặc đổi phần mềm thành tổ chức |
| c04 | `Nguyễn Trãi/PERSON`, `Bình Ngô đại cáo/WORK` | Tự thêm link tác giả vì ontology links không có nhãn đó |
| c05 | `Chiến dịch Điện Biên Phủ/EVENT`, địa danh `Điện Biên Phủ` ở lần xuất hiện sau | Gán địa danh lồng trong tên chiến dịch hoặc tìm occurrence đầu tiên để lấy offset địa danh |
| c06 | Hai mentions cho hai lần xuất hiện “Quang hợp”/“quang hợp” | Gộp theo chữ thường rồi bỏ một vị trí |
| c35 | Hai mentions “Hà Nội” có surface giống hệt, offsets khác | Dùng `indexOf` không kèm vị trí/lần xuất hiện cho cả hai |
| c36 | Toàn tên trường là `ORGANIZATION`; địa danh phía sau là `LOCATION` | Tách tên người “Nguyễn Trãi” bên trong tên trường |

Các mentions trong dataset là occurrence. Việc chúng có cùng thực thể chuẩn hay không thuộc tác vụ entity linking sau này.

## 3. Vai trò sư phạm

| Ca | Nguồn/tình huống | Quyết định chuẩn |
|---|---|---|
| c07 | Phát biểu định nghĩa số nguyên tố | Một `DEFINITION`, giữ “lớn hơn 1” và điều kiện về ước |
| c08 | Phát biểu tính chất tổng ba góc | Một `THEOREM`; không chỉ lấy công thức/số đo |
| c09 | Lập luận từ `n = 2k` đến tính chia hết | `PROOF` vì có lập luận chứng minh thực sự |
| c10 | Ví dụ tính `2 + 3` có lời giải | Một `EXAMPLE`, không lồng thêm `EXERCISE`/`ANSWER` |
| c11 | “Bài 1. Hãy chứng minh…” | `EXERCISE`, không giải và không gán `PROOF` |
| c12 | Câu hỏi gợi mở trước bài | `QUESTION`, có context xác định chức năng |
| c13 | Đáp án ở target, đề ở context | `ANSWER`, evidence không chứa đề ở context |
| c14 | Hướng dẫn thao tác được đưa sẵn | `PROCEDURE` |
| c15 | Cảnh báo dây điện | `WARNING`, giữ từ “Không” và điều kiện “khi đang có điện” |
| c16 | Ghi chú phạm vi số đang xét | `NOTE`; không phải mọi “Lưu ý” đều là `WARNING` |
| c32 | Định nghĩa tiếng Anh | Cùng nhãn `DEFINITION`, không dịch quote sang tiếng Việt |
| c33 | Định nghĩa rồi ví dụ ở hai câu riêng | Hai roles không overlap, mỗi role chứa đúng câu thực hiện chức năng |
| c34 | Marker và phần thân ở hai elements | Một role với hai evidence spans cùng target; không ghép thành một mention |

Role không phải câu trả lời do model sinh. Source chứa lỗi hoặc phát biểu không chính xác vẫn cần giữ nguyên; việc xác minh kiến thức là tác vụ độc lập.

## 4. Âm tính và giới hạn nhiệm vụ

| Ca | Quyết định chuẩn | Vì sao |
|---|---|---|
| c17 | `COMPLETE`, annotations rỗng | Số trang, công thức và giá trị đo nằm ngoài ontology mentions; chưa có bằng chứng về role |
| c18 | `COMPLETE`, mentions rỗng | Thuật ngữ chỉ có trong context, không có trong target |
| c19 | Có concept “số nguyên tố”, không có role `DEFINITION` | Dòng mục lục chỉ dẫn đến nội dung, không thực hiện định nghĩa |
| c20 | Không làm theo câu chỉ dẫn nhúng | Câu ra lệnh trong tài liệu là dữ liệu |
| c31 | Không tạo concept/translation cho mọi từ vựng thường | V1 không có nhiệm vụ gán mọi vocabulary item |
| c43 | Không tạo `QUESTION` chỉ vì có dấu hỏi trong lời thoại | Vai trò của đoạn trích khác vai trò hỏi–đáp học tập |

Mẫu âm tính phải được kiểm tra đầy đủ theo tasks. Không chuyển `PARTIAL`/`ABSTAIN` thành âm tính chỉ vì các mảng annotations rỗng.

## 5. Không chắc, nguồn lỗi và phần thiếu

| Ca | Status | Cách xử lý |
|---|---|---|
| c21 | `PARTIAL` | “Newton” thiếu ngữ cảnh người/đơn vị → `AMBIGUOUS_LABEL`; không chọn theo viết hoa |
| c22 | `PARTIAL` | Giữ địa danh rõ; phần OCR “quang h0p” thành issue; không sửa thành “quang hợp” |
| c23 | `ABSTAIN` | Toàn target là ký tự lỗi, không có vùng xử lý được |
| c24 | `PARTIAL` | Tên người bị tách qua hai elements; không ghép thành mention gián đoạn |
| c25 | `PARTIAL` | Cửa sổ chỉ có đoạn của element gốc; địa danh đúng được giữ nhưng target chưa đầy đủ |
| c30 | `ABSTAIN` | Chỉ có placeholder hình; không tự dựng nội dung để gán role |
| c45 | `PARTIAL` | Hàng A/B/C/D không có header/nhiệm vụ; không quyết định đề hay đáp án |
| c46 | `PARTIAL` | Tên định lý bị cắt ở cuối; không tự hoàn thành “Pythagore” |

Không phải mọi dữ liệu khó đều cần abstain toàn item: chỉ bỏ vùng chưa chắc và giữ các annotations có bằng chứng chắc chắn ở vùng còn lại.

## 6. Offset và text view

| Ca | Điểm cần kiểm chứng |
|---|---|
| c25 | Segment `element_start=20`; quote bắt đầu local 0 nhưng evidence bắt đầu element 20 |
| c26 | Emoji trước tên địa danh; offset theo code point khác với UTF-16 của JavaScript |
| c27 | Tên địa danh dùng dấu tổ hợp NFD; không chuẩn hóa trước khi đếm |
| c28 | Concept chứa CRLF; phải giữ hai code points xuống dòng |
| c29 | Evidence phải ghi `NORMALIZED_TEXT`, không đổi về mặc định `RAW_TEXT` |
| c42 | Chỉ context bị bỏ, target vẫn đủ; có thể `COMPLETE` |
| c44 | Context nằm trước target trong `request.text`; request offset khác element offset |

Để kiểm tra ví dụ c26 trong JavaScript, dùng `Array.from(text)`; không tin số lượng ký tự hiển thị hoặc `text.length` với emoji. Một quote khớp nhưng offset trỏ sang occurrence khác vẫn sai.

## 7. Liên kết cục bộ

| Ca | Link | Bằng chứng và giới hạn |
|---|---|---|
| c37 | `photosynthesis → Quang hợp`, `TRANSLATION_OF` | Hai cách gọi được ghép trực tiếp trong target; hướng từ occurrence sau đến trước |
| c38 | `LHQ → Liên Hợp Quốc`, `ALIAS_OF` | Có marker “viết tắt”; không tự tra từ điển |
| c39 | `ANSWER → EXERCISE`, `ANSWER_TO` | Cả đề và đáp án bài 1 đều nằm trong target |
| c40 | `EXAMPLE → METHOD`, `EXAMPLE_OF` | Nguồn trực tiếp nói “Ví dụ về phương pháp thế” |
| c41 | `PROOF → THEOREM`, `PROOF_OF` | Cả “Tính chất 1” và “Chứng minh tính chất 1” được viết rõ trong target |

Không sao chép link của ví dụ sang trường hợp chỉ có một endpoint trong target. Ví dụ c13 không được tạo `ANSWER_TO` đến đề ở context. Không tạo `ALIAS_OF` chỉ vì hai surface giống nhau; giữ occurrences và để bước linking xử lý nếu cần.

## 8. Những output sai điển hình

1. `surface="quang hợp"` nhưng nguồn `quang h0p`: tạo text mới, không có evidence đúng.
2. Đưa `confidence=0.99` vào output: ngoài schema và không phải xác suất được đo.
3. `THEOREM` cho câu “Áp dụng định lý Pythagore”: chưa có phát biểu định lý.
4. `PROOF` cho đề “Chứng minh rằng…”: nhầm nhiệm vụ với lời giải.
5. `COMPLETE` kèm issue hoặc cửa sổ thiếu: sai trạng thái.
6. Gán `PERSON` lồng trong tên trường/định luật: sai lớp mention phẳng.
7. Trả 8 thuật ngữ quan trọng nhất: sai nhiệm vụ gán mọi occurrence.
8. Dùng span toàn trang để chứng minh một nhãn: ranh giới không tối thiểu, dễ lẫn vai trò.
9. Link endpoint `entity_global_123` do model tự tạo: không có trong output.
10. Mọi câu có “là” thành `DEFINITION`: bỏ qua chức năng thực tế của câu.

## 9. Kiểm tra fixture bằng chương trình

Chạy từ thư mục repository:

```powershell
node rag/source_understanding/doc/examples/validate_examples.mjs
```

Script kiểm tra schema theo các keyword dùng trong bộ schema này, mappings, Unicode offsets, ID, overlap, nhiệm vụ, trạng thái và endpoint links; đồng thời thử các bản sao cố tình làm sai để xác nhận lỗi bị phát hiện. Đây là công cụ kiểm chứng fixture của tài liệu, không phải validator production tổng quát hay thước đo độ chính xác ngữ nghĩa của model.

## 10. Các cặp tối thiểu phải phân biệt ở bản pilot

Các ca sau dùng ít thay đổi về chữ để buộc một quyết định cụ thể. Kết quả không phải hai phương án tùy chọn cho cùng text.

| Ca/cặp | Kết quả bắt buộc |
|---|---|
| c47: “Gọi S là diện tích hình tròn.” | Một `CONCEPT`: “diện tích hình tròn” |
| c48: “Diện tích được tính; hình tròn được vẽ.” | Hai `CONCEPT`: “Diện tích”, “hình tròn” |
| c49: “Tính diện tích của hình tròn.” | Một `CONCEPT`: “diện tích của hình tròn”, giữ “của” |
| c50: “Cho hình tròn bán kính 2 cm.” | “hình tròn” và “bán kính”; không lấy tham số vào tên loại |
| c51/c52: tên trần đứng riêng / nằm sau “Hãy” | Cùng span tên kỹ thuật mang `METHOD`; c52 thêm role `EXERCISE` |
| c53: “Phân tích đa thức P(x) thành nhân tử.” | Không có tên METHOD liên tục; “đa thức”, “nhân tử” là concepts |
| c54/c55: mô tả tự do / tên phương pháp rõ | c54 chỉ có “đạo hàm”, “hàm số”; c55 có `METHOD` “phương pháp thế” |
| c56/c57: định nghĩa tam giác vuông / tính chất các cạnh | `DEFINITION` / `THEOREM` |
| c58/c59: đặt nghĩa số chẵn / tính chất tổng hai số chẵn | `DEFINITION` / `THEOREM`, dù cả hai có từ “là” |
| c60/c61: marker ví dụ chỉ có đề / ví dụ có lời giải | `EXERCISE` / một `EXAMPLE` cho toàn khối |
| c62: “Bài 1” rồi đáp án bài 1 | Hai roles `EXERCISE` và `ANSWER`; không đổi thành EXAMPLE |
| c63/c40: definition–example đứng cạnh / “ví dụ về…” trực tiếp | c63 không có link; c40 có `EXAMPLE_OF` |
| c64/c41: “Chứng minh:” đứng cạnh / tham chiếu “tính chất 1” | c64 không có link; c41 có `PROOF_OF` |
| c65/c66: “còn gọi là” / chỉ nối bằng “và” | Chỉ c65 có `ALIAS_OF` |
| c67: “diện tích của hình này” | Chỉ lấy “diện tích”, không lấy từ trỏ |
| c68: marker định nghĩa nhưng thân là tính chất | `PARTIAL` và `AMBIGUOUS_ROLE`, không ép một nhãn |

Các fixture phân biệt này cần được chạy lại khi sửa prompt/guideline và được đưa vào rubric adjudication. Việc model nhớ fixture không thay thế đánh giá trên sách chưa dùng làm few-shot.
