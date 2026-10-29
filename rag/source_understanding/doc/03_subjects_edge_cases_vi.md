# 03 — Quy tắc theo môn học, ngôn ngữ và dạng tài liệu

Hợp đồng: `edu-annotation-1.1-pilot`. Tất cả quy tắc ở đây dùng chung ontology của 02; không tạo nhãn riêng cho từng môn. Không suy đoán lớp từ độ khó hoặc từ kiến thức thường gặp trong một chương trình.

## 1. Toán học

| Tình huống | Quyết định |
|---|---|
| “Cho tam giác vuông ABC.” | `CONCEPT` = “tam giác vuông”; “ABC” là ký hiệu, bỏ |
| “Tính đạo hàm của hàm số…” | Gán các thuật ngữ hiện diện; toàn nhiệm vụ là `EXERCISE` nếu được giao cho học sinh |
| “Áp dụng định lý Pythagore…” | `NAMED_PRINCIPLE` cho tên; không có `THEOREM` nếu chưa phát biểu định lý |
| “Nếu … thì …” trong khung tính chất/định lý | `THEOREM`, giữ cả điều kiện và kết luận |
| “Chứng minh rằng…” trong đề | `EXERCISE`, không tạo `PROOF` |
| Lập luận thực sự kết thúc bằng “Vậy …”, QED | `PROOF` nếu mục đích chứng minh rõ; một chữ “vậy” chưa đủ |
| “Bước 1… Bước 2…” hướng dẫn phương pháp tổng quát | `PROCEDURE` |
| Một ví dụ có đề và lời giải đầy đủ | `EXAMPLE` cho khối minh họa; mentions vẫn được gán bên trong |

Công thức như `a² + b² = c²` hoặc LaTeX không phải mention. Nếu nó là một phần của phát biểu/định nghĩa/lời giải, giữ nguyên trong evidence role. Không chuyển LaTeX sang Unicode, không thêm dấu ngoặc, không sửa vế phương trình. Nếu chỉ có ảnh công thức nhưng không có text tương ứng, không tự đọc từ trí nhớ hoặc mô tả ảnh không được cung cấp.

Tên ký hiệu và đại lượng khác nhau: trong “Gọi S là diện tích hình tròn.”, **bắt buộc** gán đúng một `CONCEPT` là “diện tích hình tròn”; “S” không gán. Không chấp nhận phương án tách “diện tích” và “hình tròn” trong cùng cụm này. “Tính diện tích của hình tròn.” cũng giữ toàn cụm “diện tích của hình tròn”. Ngược lại, “Diện tích được tính; hình tròn được vẽ.” có hai mentions vì hai thuật ngữ có vị ngữ riêng. Xem các mẫu span bắt buộc ở 02, mục 1.1.

Tên kỹ thuật “phân tích đa thức thành nhân tử” giữ nhãn `METHOD` cả khi đứng trong câu “Hãy phân tích đa thức thành nhân tử.”; toàn câu là role `EXERCISE`. Nhưng “Phân tích đa thức P(x) thành nhân tử.” không có tên phương pháp liên tục: chỉ gán `CONCEPT` “đa thức” và “nhân tử”. Không tái dựng mention qua ký hiệu P(x). Quy tắc tên trần và tên thao tác nằm ở 02, mục 1.3.

## 2. Vật lý

- Đại lượng bằng lời trong nghĩa chuyên môn có thể là `CONCEPT`: lực, công, công suất, điện trở, hiệu điện thế.
- Phải đọc ngữ cảnh: “công” trong “có công giúp đỡ” không là thuật ngữ Vật lý.
- Tên đơn vị bằng lời khi đang được giảng như một khái niệm có thể là `CONCEPT`; ký hiệu đơn vị trong giá trị đo `5 N`, `12 V` không là mentions.
- “Newton” là `PERSON` khi nói về người; “newton” là `CONCEPT` khi trực tiếp giảng đơn vị; `N` đứng riêng không gán.
- “định luật Ohm” là `NAMED_PRINCIPLE`; dòng `I = U/R` chỉ có thể tham gia role khi có ngữ cảnh phát biểu rõ.
- Giữ điều kiện áp dụng định luật trong role. Không tách mất giả thiết, ví dụ trạng thái hệ hoặc phạm vi nhiệt độ.

## 3. Hóa học

- Tên chất bằng lời, nhóm chất, loại phản ứng và cấu trúc vật chất trong ngữ cảnh chuyên môn → `CONCEPT`.
- `H2O`, `H₂O`, `NaCl`, ion, hệ số phản ứng và phương trình hóa học → không gán mention trong V1.
- “nước (H₂O)” → mention “nước” nếu đang giảng chất; không tạo alias link tới công thức vì công thức không phải node mention của ontology này.
- “định luật bảo toàn khối lượng” → `NAMED_PRINCIPLE`.
- Hướng dẫn thí nghiệm có các thao tác được đưa sẵn → `PROCEDURE`. Đề yêu cầu học sinh tự thiết kế thí nghiệm → `EXERCISE`.
- Cảnh báo hóa chất/nguy hiểm → `WARNING`; giữ nguyên từ cấm và điều kiện. Không tự bổ sung lời khuyên an toàn ngoài nguồn.

## 4. Sinh học và khoa học tự nhiên

- Quá trình, cấu trúc, phân loại và hiện tượng đang được học → `CONCEPT`: “quang hợp”, “hô hấp tế bào”, “hệ sinh thái”, “tế bào nhân thực”.
- Tên loài bằng tiếng Việt hoặc tên khoa học Latin → `CONCEPT` khi đối tượng đang được trình bày theo nghĩa sinh học. Không gán `PERSON` vì tên Latin viết hoa.
- Tên một con vật có tên riêng trong truyện → `PERSON` theo quy ước nhân vật có tên; tên loài chung trong truyện không tự thành thuật ngữ sinh học.
- Các giai đoạn một quá trình tự nhiên được giải thích không tự là `PROCEDURE`. `PROCEDURE` dành cho chỉ dẫn thực hiện; mô tả “hạt nảy mầm rồi…” có thể không có role.
- Sơ đồ/hình có nhãn không cung cấp bằng text: issue `MISSING_MODALITY` nếu thiếu làm không hoàn thành target. Không tự đoán nhãn từ tên bài.

## 5. Ngữ văn

- Tác giả/nhân vật có tên → `PERSON`; tác phẩm có nhan đề → `WORK`; thuật ngữ nghệ thuật/ngữ pháp → `CONCEPT`.
- “Lão Hạc” trong ngữ cảnh tác phẩm và trong ngữ cảnh nhân vật có nhãn khác nhau; xét từng lần xuất hiện.
- Lời thoại của nhân vật có dấu hỏi không mặc định là role `QUESTION`. Câu hỏi của phần hướng dẫn đọc hiểu nằm ngoài đoạn trích mới có thể là `QUESTION` hoặc `EXERCISE`.
- Đoạn thơ được dẫn làm ví dụ biện pháp tu từ → `EXAMPLE` nếu ngữ cảnh xác định chức năng minh họa; đoạn thơ chỉ là văn bản đọc chính không tự thành ví dụ.
- Yêu cầu “Phân tích hình ảnh…” → `EXERCISE`. Không tự viết bài phân tích.
- Một bài văn nghị luận lập luận không tự là `PROOF`; `PROOF` cần ngữ cảnh chứng minh mệnh đề theo nghĩa được quy định, không chỉ có lời thuyết phục.
- Từ đang được đưa ra làm đối tượng phân tích trong ngoặc kép không tự là concept. Trong “Tìm từ láy trong câu…”, “từ láy” là concept, từng từ của câu trích không được nâng thành thuật ngữ.

## 6. Lịch sử, Địa lý và GDCD

- Phân biệt người, địa danh, tổ chức, sự kiện và văn bản: `PERSON`, `LOCATION`, `ORGANIZATION`, `EVENT`, `WORK`.
- “Điện Biên Phủ” là `LOCATION` khi nói về địa điểm; “chiến dịch Điện Biên Phủ” là `EVENT` và không gán địa danh lồng bên trong.
- Ngày/tháng/năm đứng riêng không gán; năm nằm trong tên văn bản/sự kiện được giữ khi là phần nhận diện tên trong nguồn.
- Khái niệm kinh tế, thể chế, quyền/nghĩa vụ, hiện tượng địa lý → `CONCEPT` khi ngữ cảnh chuyên môn rõ.
- Trích dẫn quy định pháp luật không tự là `THEOREM`. Câu định nghĩa thuật ngữ pháp lý có thể là `DEFINITION`; văn bản quy định thông thường có thể không có role trong V1.
- Câu “Hà Nội là thủ đô Việt Nam” chứa địa danh nhưng không tự là định nghĩa hay định lý.
- Không suy ra sự kiện, quan hệ tác giả, quan hệ nguyên nhân hoặc tên quốc gia từ mốc thời gian/địa điểm.

## 7. Tin học và Công nghệ

- Tên thuật toán/phương pháp → `METHOD`; đối tượng như biến, kiểu dữ liệu, vòng lặp → `CONCEPT`.
- Phần mềm cụ thể → `PRODUCT`; công ty tạo phần mềm → `ORGANIZATION` khi xuất hiện độc lập.
- Mã nguồn, tên biến/hàm, đường dẫn, URL, tag HTML, cú pháp `for`, `if`, `print` trong code không được gán mentions theo ontology này.
- Từ “vòng lặp for” được giảng trong văn xuôi có thể là `CONCEPT`; không gán mọi token `for` trong code.
- Comments/code strings chỉ được gán nếu target là đoạn giải thích ngôn ngữ tự nhiên và phần được chọn thực sự gọi tên kiến thức; tránh quét literals/dữ liệu mẫu như văn xuôi mặc định.
- Code được dùng làm ví dụ có thể nằm trong evidence `EXAMPLE`; code solution cho bài tập có thể thuộc `ANSWER`, khi chức năng được xác định từ nguồn.

## 8. Tiếng Anh và tài liệu song ngữ

### 8.1. Dùng cùng ontology

“photosynthesis” và “quang hợp” đều có thể là `CONCEPT`. “Pythagorean theorem” và “định lý Pythagore” đều là `NAMED_PRINCIPLE`. Không tạo nhãn theo ngôn ngữ và không dịch surface.

Đối với tên riêng, giữ cách viết đúng nguồn, kể cả phiên âm. “Niu-tơn” không được đổi thành “Newton”. Hai cách viết chỉ được liên kết khi target trực tiếp chứng minh chúng đồng chỉ; không dùng trí nhớ của model để tạo link.

### 8.2. Bài học tiếng Anh

- “present perfect”, “conditional sentence” trong phần giảng ngữ pháp → `CONCEPT`.
- Danh sách từ vựng `apple — quả táo`, `book — quyển sách` là các từ thông thường được học; V1 không có nhãn `VOCABULARY_ITEM`, do đó không tự coi tất cả là `CONCEPT` và không tạo translation links cho chúng.
- Nếu cần học từ vựng tổng quát, phải mở rộng ontology có phiên bản riêng. Không dùng `CONCEPT` để âm thầm mở rộng nhiệm vụ.
- “Translate the following sentence…” → `EXERCISE`; không dịch câu đó.
- Câu hỏi trong hội thoại được in như ví dụ sử dụng ngôn ngữ thuộc khối `EXAMPLE` khi chức năng minh họa rõ; không tách mỗi dấu hỏi thành một `QUESTION` lồng bên trong.

### 8.3. Tương đương liên ngôn ngữ

“Quang hợp (photosynthesis) …” cho phép hai mentions riêng và một `TRANSLATION_OF` nếu cấu trúc và nghĩa tương đương rõ. Evidence link phải bao cả hai cách gọi và dấu hiệu ghép cặp. Hai đoạn song ngữ nằm gần nhau không đủ để tự nối mọi từ tương ứng.

Viết tắt như “ADN” được dùng rõ như tên khái niệm → `CONCEPT`; không tự mở rộng thành tên đầy đủ. Nếu target cho “axit deoxyribonucleic (ADN)” và quan hệ viết tắt rõ, có thể tạo `ALIAS_OF`.

## 9. Tiêu đề, mục lục, tài liệu tham khảo và phần phụ

Mentions trong heading, mục lục, bibliography, caption và footer vẫn được gán nếu đáp ứng ontology. Nguồn phụ không làm tên riêng mất nhãn; việc có đưa vùng đó vào retrieval là chính sách downstream.

Tuy nhiên, không suy role từ vùng phụ:

- Mục lục “Định nghĩa … 12”, “Bài tập … 24” không chứa định nghĩa/bài tập thực tế.
- Bibliography có tên tác giả/tác phẩm nhưng không phải role `EXAMPLE` hay `ANSWER`.
- Heading “Chứng minh” không có phần thân → không có `PROOF`. Nếu target bị cắt mất thân, `PARTIAL`; nếu target hoàn chỉnh chỉ là heading, `COMPLETE` không role.
- Caption “Hình 2. Tế bào thực vật” có thể chứa concept, không tự là định nghĩa.

## 10. Bảng biểu

Chỉ sử dụng text và cấu trúc hàng/cột được cung cấp. Không suy cột/hàng từ một chuỗi bị đảo thứ tự.

- Bảng “Thuật ngữ | Định nghĩa”: cell thuật ngữ có mention; cell nội dung có thể có role `DEFINITION`. Nếu hai cell nằm trong target cùng logical unit, role có thể gồm cả nhãn thuật ngữ và định nghĩa; không tạo mention vượt cell.
- Bảng “Câu | Đáp án”: chỉ gán `ANSWER` khi header/context xác định đây là đáp án và biết cell thuộc câu nào. `A`, `B`, `C` trong bảng không mặc định là đáp án.
- Bảng số liệu không tự thành role; thuật ngữ ở header có thể là concept nếu đủ nghĩa.
- Nếu parser làm mất liên hệ header–cell và điều đó ảnh hưởng nhãn, issue `MISSING_CONTEXT`, không tự tái dựng bảng.

## 11. OCR, Unicode và chất lượng nguồn

| Trường hợp | Hành vi |
|---|---|
| Text đúng nhưng khác cách viết quen thuộc | Giữ nguyên, gán nếu nghĩa rõ |
| Một tên bị mất dấu nhưng vẫn xác định rõ loại và span | Gán nguyên văn; không sửa dấu; không cần issue chỉ vì không có dấu |
| `quang h0p`, `Nguy?n Trãi` gây không chắc cách đọc/loại | Không sửa; issue `OCR_UNREADABLE` trên vùng lỗi; giữ nhãn khác rõ |
| Unicode tổ hợp NFD | Đếm theo code point thực tế, không NFC hóa trước tính offset |
| CRLF, tab, nhiều khoảng trắng | Giữ nguyên trong text/quote; mỗi code point được tính theo 04 |
| Tên gãy qua hai elements | Không ghép; `CROSS_SEGMENT_MENTION` |
| Text bị cắt ở đầu/cuối cửa sổ | Không hoàn thành từ; `TRUNCATED_TARGET` khi ảnh hưởng target |
| Chỉ thiếu context tùy chọn và nhãn vẫn rõ | Có thể `COMPLETE`; không tự tạo issue |
| Cần hình/sơ đồ nhưng input chỉ có ký hiệu giữ chỗ | `MISSING_MODALITY`; không tạo nhãn từ kiến thức đoán |

Không dùng “sửa nhẹ cho dễ đọc” trong evidence. Giữ một nhãn chính xác trên text xấu tốt hơn một nhãn đẹp nhưng không truy vết được; khi không chắc thì abstain ở vùng đó.

## 12. Nội dung sai, phủ định và phát biểu được trích dẫn

Dataset này gán chức năng và biểu thức của nguồn, không chứng nhận tính đúng khoa học. Nếu sách viết một phát biểu sai nhưng thể hiện rõ là định nghĩa, vẫn có thể gán `DEFINITION` với nguyên văn. Quy trình kiểm tra kiến thức là tác vụ khác.

Phải xét phạm vi phủ định/trích dẫn. “Sai lầm thường gặp: mọi số nguyên tố đều lẻ” là `WARNING` nếu đang cảnh báo nhận định sai, không đồng thời là `THEOREM`. Tên thuật ngữ trong câu vẫn được gán. “Câu nào sau đây sai?” thuộc `EXERCISE`; các lựa chọn là phần của đề, không tách thành các định lý được khẳng định.
