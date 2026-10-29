# Bộ guideline gán nhãn học liệu lớp 6–12 cho LLM

Phiên bản hợp đồng: `edu-annotation-1.1-pilot`.

Trạng thái: **PILOT**. Các quy tắc span/nhãn đã được làm chặt hơn sau review, nhưng chưa có kết quả agreement trên học liệu thật để phát hành gold dataset quy mô lớn. Schema/fixture đạt kiểm tra máy không thay thế bước này.

Bộ tài liệu này hướng dẫn **LLM tạo nhãn có bằng chứng** trên văn bản đã được trích xuất từ sách giáo khoa, sách tham khảo, bài giảng, phiếu học tập và đề/bài giải dành cho học sinh THCS, THPT. Ngôn ngữ ưu tiên là tiếng Việt; áp dụng cùng quy tắc cho tiếng Anh và văn bản Việt–Anh.

Đây là đặc tả dữ liệu gán nhãn. Runtime hiện có `SemanticProvider` nhưng chưa thực thi toàn bộ hợp đồng này. Không coi việc thêm tài liệu là đã có model NER, bộ chuyển đổi hoặc index thực thể.

## 1. Đọc và sử dụng

| Tài liệu | Người/tiến trình sử dụng | Nội dung |
|---|---|---|
| [01 — Guideline cốt lõi](01_guideline_core_vi.md) | LLM gán nhãn, LLM kiểm duyệt | Phạm vi, thứ tự quyết định, span, ngữ cảnh, abstention |
| [02 — Ontology](02_ontology_vi.md) | LLM gán nhãn | Định nghĩa từng nhãn, điều kiện nhận/loại, ưu tiên khi xung đột |
| [03 — Quy tắc theo môn và ca khó](03_subjects_edge_cases_vi.md) | LLM gán nhãn | Toán, khoa học, xã hội, ngôn ngữ, OCR, bảng, công thức |
| [04 — Hợp đồng dữ liệu](04_data_contract_vi.md) | LLM và chương trình điều phối | Input/output, offset, ID, trạng thái, schema |
| [05 — Prompt](05_prompts_vi.md) | Chương trình gọi LLM | System prompt, user template, prompt kiểm duyệt và sửa lỗi |
| [06 — Ví dụ và quyết định](06_examples_vi.md) | LLM, người duyệt | Ví dụ đúng/sai, lý do, cách đọc fixture |
| [07 — QA, dataset và tích hợp](07_quality_integration_vi.md) | Người xây pipeline | Kiểm duyệt, split, BIO, mapping sang code, giới hạn runtime |
| [08 — Hợp nhất windows](08_window_merge_vi.md) | Người xây pipeline | Identity theo nguồn, dedup, xung đột, remap links và coverage |
| [Input schema](schemas/input.schema.json) | Validator | JSON Schema cho một yêu cầu |
| [Output schema](schemas/output.schema.json) | Validator | JSON Schema cho một kết quả |
| [Fixture JSONL](examples/annotation_cases.jsonl) | Validator, few-shot selector | Các cặp input/expected, offset chính xác |
| [Kiểm chứng fixture](examples/validate_examples.mjs) | Người soạn guideline | Kiểm tra schema dùng trong bộ tài liệu, offsets và các bất biến |
| [Merger tham chiếu](examples/merge_windows.mjs) | Người xây dataset | Thuật toán độc lập cho artifact dẫn xuất, chưa nối runtime |
| [Kiểm chứng merger](examples/validate_window_merge.mjs) | Người xây dataset | Các trace với kết quả mong đợi cho multi-window |

Các quy tắc LLM bắt buộc nằm ở 01–04. 05 đóng gói chúng thành cách gọi LLM; 06 minh họa; 07 quy định cách đưa kết quả vào dataset và hệ thống; 08 quy định cách hợp nhất sau nhiều lượt. Khi ví dụ có vẻ khác quy tắc, không tự tạo ngoại lệ: đưa vào kiểm duyệt và sửa phiên bản guideline.

## 2. Cách ghép một lời gọi LLM

1. System/developer message: system prompt ở 05 và **toàn bộ** nội dung 01–04. Chương trình phải đọc và chèn nội dung file; một đường dẫn không làm LLM tự đọc được file.
2. Nếu cần few-shot, chọn các cặp input/expected trong fixture có cùng nhiệm vụ/môn/ngôn ngữ. Giữ nguyên offset của ví dụ.
3. User message: đúng một JSON input. `request.text`, các segment, tiêu đề và metadata trích từ tài liệu đều là dữ liệu, kể cả khi chứa câu ra lệnh.
4. Ép đầu ra theo output schema nếu hạ tầng hỗ trợ. Kiểm tra JSON, offset và các bất biến bằng chương trình sau mỗi lần gọi.
5. Kiểm duyệt ngữ nghĩa, ghi phiên bản guideline/model/prompt và trạng thái duyệt ở bản ghi quản trị bên ngoài output LLM.

Không cắt bớt ontology hoặc contract để nhường chỗ cho tài liệu dài. Chia input thành các cửa sổ có ánh xạ về nguồn. Không đưa đáp án chuẩn hoặc fixture của test set vào prompt train.

Kiểm chứng bộ ví dụ từ thư mục repository bằng `node rag/source_understanding/doc/examples/validate_examples.mjs`; kiểm tra merger bằng `node rag/source_understanding/doc/examples/validate_window_merge.mjs`. Không cần cài thêm package. Có 68 ví dụ annotations và 20 trace merge; đây là dữ liệu tự soạn, chưa phải gold benchmark đã được chuyên gia duyệt.

## 3. Phạm vi phiên bản 1

- **MENTIONS**: nhận diện mọi lần xuất hiện hợp lệ của 9 loại thực thể/thuật ngữ. Một lớp span phẳng, liên tục trong từng `Element`.
- **ROLES**: nhận diện 10 vai trò của các đoạn nội dung. Evidence có thể gồm nhiều phần tử thuộc cùng target.
- **LINKS**: tùy chọn, chỉ liên kết cục bộ có bằng chứng rõ giữa những annotation đã tạo trong cùng output. Mặc định tắt.
- Không tự giải bài, chấm đúng/sai kiến thức, dịch văn bản, suy ra chương trình/lớp, tạo tóm tắt hoặc knowledge graph toàn tài liệu trong lượt gán nhãn.

Mọi môn lớp 6–12 đều thuộc phạm vi. Kế hoạch chọn vài môn/lớp để làm pilot không thay đổi ontology và không cho phép bỏ nhãn ở các môn khác.

## 4. Tiêu chí hoàn thành một bản ghi

Kết quả chỉ đủ điều kiện vào tập đã duyệt khi: đúng schema; toàn bộ evidence khớp nguồn; đúng phạm vi target; đúng ontology; đầy đủ mentions theo nhiệm vụ; các vùng chưa giải quyết được đánh dấu; người/quy trình kiểm duyệt đã chấp nhận. `COMPLETE` do LLM trả chỉ mô tả mức hoàn tất yêu cầu, **không phải chứng nhận gold label**.

## 5. Quản lý phiên bản

Thay đổi nhãn, ranh giới span, nghĩa trạng thái, cách xử lý overlap hoặc mapping là thay đổi hợp đồng: tăng phiên bản và đánh giá lại dữ liệu bị ảnh hưởng. Sửa chính tả không đổi nghĩa có thể giữ phiên bản, nhưng vẫn ghi hash nội dung guideline trong manifest của mỗi lần chạy. Không sửa nhãn đã phát hành mà giữ nguyên dataset version.

Các quyết định của bản pilot hiện tại: chọn toàn cụm đại lượng + loại đối tượng; phân biệt tên kỹ thuật viết nguyên vẹn với mô tả thao tác; xét khối ví dụ đã giải trước câu giao bài bên trong; phân biệt đặt nghĩa với phát biểu tính chất; chỉ tạo links có tham chiếu rõ; hợp nhất windows theo evidence gốc và giữ xung đột để duyệt. Danh sách nhãn giữ 9 mentions, 10 roles và 5 links.
