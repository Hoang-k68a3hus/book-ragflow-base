# 02 — Ontology và quy tắc phân biệt nhãn

Hợp đồng: `edu-annotation-1.1-pilot`. Đây là danh sách đóng. Không tự thêm nhãn cho một trường hợp không khớp.

## 1. Mentions: 9 nhãn

Mỗi mention có đúng một nhãn, một surface và một evidence span liên tục trong một element. Nhãn viết hoa dưới đây là nhãn dataset; mapping sang `SemanticAnnotationType` nằm ở 07.

### 1.1. CONCEPT — Khái niệm, đối tượng hoặc hiện tượng chuyên môn

**Nhận:** một cụm từ gọi tên đơn vị kiến thức mà học sinh có thể học định nghĩa, thuộc tính hoặc quan hệ; nghĩa chuyên môn được thể hiện trong ngữ cảnh hiện tại. Có thể là danh từ một từ nếu đủ rõ.

- Toán: “số nguyên tố”, “hàm số bậc hai”, “tam giác vuông”, “đạo hàm”.
- Lý/Hóa/Sinh: “điện trở”, “phản ứng oxi hóa”, “quang hợp”, “tế bào”, “oxygen” khi được dùng như tên chất.
- Ngữ văn/ngôn ngữ: “ẩn dụ”, “từ láy”, “present perfect”, “câu điều kiện”.
- Sử/Địa/GDCD: “chế độ phong kiến”, “khí hậu nhiệt đới”, “quyền công dân”.
- Tin học: “biến”, “vòng lặp”, “cơ sở dữ liệu”, khi đang được dùng theo nghĩa Tin học.

**Loại:** từ chung không mang nghĩa chuyên môn trong lần xuất hiện hiện tại; tên riêng đã có nhãn riêng; biến/số/ký hiệu; toàn bộ câu định nghĩa; hoạt động bất kỳ của học sinh.

Ví dụ quyết định:

| Text/ngữ cảnh | Gán | Không gán |
|---|---|---|
| “Lực làm vật biến đổi chuyển động.” trong bài Vật lý | “Lực” nếu dùng như đại lượng vật lý | Không lấy cả câu làm concept |
| “Em cần nỗ lực hơn.” | Không có | Không gán chuỗi con “lực” |
| “Nước là một hợp chất…” trong bài Hóa | “Nước”, “hợp chất” | Không đổi “Nước” thành “H₂O” |
| “Uống nước trước khi làm bài.” trong lời nhắc sinh hoạt | Không có | Không tự áp nghĩa Hóa học |
| “Truyện sử dụng phép ẩn dụ.” | “phép ẩn dụ” | Không lấy “Truyện” nếu chỉ là danh từ chỉ văn bản |
| “Cho tam giác vuông ABC…” | “tam giác vuông” | Không lấy “ABC” vào tên khái niệm |

**Ranh giới bắt buộc:** áp dụng các mẫu dưới đây. Khi một mẫu đã khớp, không được chọn phương án ngắn hơn/dài hơn chỉ vì cũng hợp lý về nghĩa.

| Mẫu | Quyết định mặc định bắt buộc | Ví dụ |
|---|---|---|
| Đại lượng + loại đối tượng mà đại lượng được xác định trên đó | Một `CONCEPT` bao toàn cụm, gồm “của” nếu có; không thêm các phần lồng bên trong | “diện tích hình tròn”, “diện tích của hình tròn”, “thể tích hình cầu” |
| Đại lượng + một vật/ký hiệu cụ thể hoặc từ trỏ | Chỉ lấy tên đại lượng; bỏ phần chỉ cá thể | “diện tích của hình này” → “diện tích”; “diện tích S” → “diện tích” |
| Hai thuật ngữ có vị ngữ riêng hoặc được liệt kê độc lập | Hai mentions; không nối qua vị ngữ/liên từ | “Diện tích được tính; hình tròn được vẽ.” → “Diện tích”, “hình tròn” |
| Thuật ngữ + tham số/số đo/nhận xét về một cá thể | Dừng trước tham số/nhận xét; thuật ngữ khác ngoài span vẫn được gán riêng | “hình tròn bán kính 2 cm” → “hình tròn”, “bán kính” |
| Tên loại có thành tố phân biệt nghĩa | Giữ toàn bộ tên loại | “tam giác vuông”, “hàm số bậc hai”, “hô hấp tế bào” |
| Danh từ chuyên môn + mức độ/đánh giá tự do | Bỏ định từ, từ mức độ và nhận xét; không tạo tên kiến thức từ miêu tả tự do | “một dung dịch rất loãng” → “dung dịch” |

“Gọi S là diện tích hình tròn.” **bắt buộc** có một mention `CONCEPT` là “diện tích hình tròn”; không chấp nhận hai mentions “diện tích” và “hình tròn” trong lần xuất hiện đó. Ngược lại, “Diện tích được tính; hình tròn được vẽ.” bắt buộc có hai mentions. Từ “Gọi”/“Tính” đứng trước không thay đổi ranh giới cụm đại lượng.

Trong câu định nghĩa `X là Y`, không lấy cả mô tả `Y` thành một thuật ngữ chỉ vì nó định nghĩa X. “Tam giác vuông là tam giác có một góc vuông.” có các mentions “Tam giác vuông”, “tam giác”, “góc vuông”; không có mention “tam giác có một góc vuông”. Role `DEFINITION` bao cả câu theo mục 3.

Các mẫu trên không khẳng định đã bao hết mọi cấu trúc tiếng Việt. Nếu một cấu trúc mới thật sự có hai cách phân tích mà các mẫu chưa giải quyết được, ghi `UNCERTAIN_BOUNDARY` và đưa vào adjudication; không tự chọn một trong hai rồi coi cả hai đều là gold hợp lệ.

### 1.2. NAMED_PRINCIPLE — Định lý, định luật, quy tắc hoặc nguyên lý có tên

**Nhận:** tên gọi nhận diện một mệnh đề/quy tắc đã được đặt tên. Bao gồm tên riêng đi kèm loại kiến thức và tên quy tắc mô tả đủ cụ thể.

- “định lý Pythagore”, “định lí Thalès”, “định luật Ohm”.
- “định luật bảo toàn khối lượng”, “nguyên lý Pascal”.
- “quy tắc bàn tay trái”, “hằng đẳng thức đáng nhớ” chỉ khi nguồn dùng đúng cụm này như tên một nhóm quy tắc; không tự gán tên từng hằng đẳng thức chưa viết ra.

**Loại:** “định lý”, “định luật này”, “quy tắc sau” đứng riêng; tên người đang được nói như người; toàn bộ phát biểu; một bước thao tác chưa là quy tắc có tên.

**Ranh giới:** lấy cả tiền tố loại kiến thức và tên phân biệt. “Theo định lý Pythagore,” → “định lý Pythagore”. Nếu nguồn chỉ viết “Pythagore” trong một danh sách các định lý và ngữ cảnh xác định rõ chức năng tên định lý, lấy đúng “Pythagore” với `NAMED_PRINCIPLE`; nếu không phân biệt được người/định lý, issue `AMBIGUOUS_LABEL`.

**Không đồng nhất với role THEOREM:** nhắc tên định luật không đủ để gán đoạn là phát biểu định luật.

### 1.3. METHOD — Phương pháp, kỹ thuật hoặc thuật toán có tên gọi

**Nhận:** cụm gọi tên cách thực hiện có thể tái áp dụng, được trình bày như phương pháp/kỹ thuật/thuật toán.

- “phương pháp thế”, “phương pháp cộng đại số”.
- “phân tích đa thức thành nhân tử” là tên kỹ thuật trong danh sách cố định bên dưới, kể cả khi nằm trong câu mệnh lệnh.
- “thuật toán tìm kiếm nhị phân”, “kĩ thuật sắc kí”.

**Loại:** “tính”, “làm”, “quan sát”, “đọc lại”, “cách này” hoặc cả câu chỉ dẫn không có tên kỹ thuật xác định. “Tính x” không tạo `METHOD` cho “Tính”.

**Quy tắc bắt buộc phân biệt tên phương pháp và thao tác:**

1. Cụm có đầu “phương pháp”, “thuật toán”, “kỹ thuật”/“kĩ thuật” và một tên phân biệt liên tục → `METHOD`, gồm cả đầu cụm: “phương pháp thế”, “thuật toán tìm kiếm nhị phân”. Đầu cụm đứng riêng hoặc “phương pháp này” không đủ.
2. Với cụm không có đầu loại, phiên bản pilot chỉ nhận các tên kỹ thuật viết nguyên vẹn sau: “phân tích đa thức thành nhân tử”, “tìm kiếm nhị phân”, “tìm kiếm tuần tự”, “sắp xếp nổi bọt”, “binary search”, “bubble sort”. So khớp không phân biệt hoa/thường nhưng giữ surface nguyên văn. Đây là danh sách đóng cho dạng tên trần, không phải danh sách tất cả phương pháp.
3. Dạng tên trần đó vẫn là `METHOD` khi được dùng như động từ trong yêu cầu. “Hãy phân tích đa thức thành nhân tử.” → mention “phân tích đa thức thành nhân tử” và role `EXERCISE`. Không đổi mention thành `CONCEPT` chỉ vì câu mệnh lệnh.
4. Nếu đưa đối tượng cụ thể vào giữa khiến tên không còn liên tục, không tái dựng tên. “Phân tích đa thức P(x) thành nhân tử.” → `CONCEPT` “đa thức” và “nhân tử”; không có `METHOD` gián đoạn và không gán động từ “Phân tích” đứng riêng.
5. Một mô tả tự do bắt đầu bằng “phương pháp dùng/sử dụng/vận dụng … để …” không được tự coi là tên kỹ thuật. Chỉ gán các khái niệm độc lập bên trong. “Phương pháp dùng đạo hàm để khảo sát hàm số.” → `CONCEPT` “đạo hàm”, “hàm số”; không có `METHOD` bao toàn câu.
6. Động tác chung “tính”, “đo”, “đọc”, “quan sát”, “thay vào” không là tên phương pháp. Một tên trần chưa có trong danh sách nhưng có tín hiệu rõ là tên kỹ thuật cần `AMBIGUOUS_LABEL` để duyệt/mở rộng registry, không ép thành `CONCEPT`. Một thao tác thông thường bị loại theo quy tắc không cần issue.

**Phân biệt CONCEPT:** đối tượng/hiện tượng/đại lượng → `CONCEPT`; tên kỹ thuật được nhận theo các bước trên → `METHOD`. “đạo hàm” là `CONCEPT`; “phương pháp thế” là `METHOD`. Trong span `METHOD`, không gán các concepts lồng bên trong vì lớp mentions phẳng. Mở rộng danh sách tên trần hoặc đổi các quy tắc này phải tăng phiên bản và duyệt lại dữ liệu liên quan.

**Phân biệt PROCEDURE:** tên cách làm là mention; các bước thực hiện là role.

### 1.4. PERSON — Người hoặc nhân vật có tên

**Nhận:** tên riêng, bút danh, bí danh hoặc tên nhân vật được văn bản xác định như một cá nhân. Áp dụng cả nhân vật văn học/lịch sử và tên giả trong bài tập.

- “Nguyễn Trãi”, “Newton”, “Lão Hạc”, “An” trong “An có 5 quyển vở”.
- Tên ngắn vẫn hợp lệ nếu cách dùng xác định đó là người.

**Loại:** “ông”, “tác giả”, “nhân vật”, “em”, chức danh không kèm tên; tên người nằm trong tên tổ chức/định luật đã chọn làm mention lớn hơn; “An” trong “an toàn”.

**Ranh giới:** bỏ danh xưng thông thường. Giữ thành tố tạo tên nhân vật/tên gọi cố định như “Lão Hạc”. Không tự diễn giải một từ là họ, tên hoặc bí danh bằng kiến thức ngoài đoạn.

### 1.5. LOCATION — Địa danh có tên

**Nhận:** quốc gia, tỉnh/thành, vùng địa lý, sông, núi, biển hoặc địa điểm được gọi tên.

- “Việt Nam”, “Hà Nội”, “sông Hồng”, “Biển Đông”, “dãy Hoàng Liên Sơn”.
- “miền Bắc Việt Nam” nếu văn bản đang dùng như tên một vùng địa lý xác định.

**Loại:** “phía bắc”, “dòng sông”, “nhà trường”, “trên hình”, “điểm A”; địa danh bên trong tên tổ chức/sự kiện đã gán ở span lớn hơn.

**Ranh giới:** giữ danh từ địa lý khi là thành phần tên gọi (“sông Hồng”). Bỏ giới từ: “ở Hà Nội” → “Hà Nội”.

### 1.6. ORGANIZATION — Tổ chức có tên

**Nhận:** cơ quan, trường học, doanh nghiệp, tổ chức quốc tế, hội/đoàn có tên riêng hoặc viết tắt được dùng như tên tổ chức.

- “Liên Hợp Quốc”, “UNESCO”, “Trường THPT Nguyễn Trãi”.
- “FPT” khi đoạn đang nói về doanh nghiệp.

**Loại:** “nhà trường”, “công ty”, “nhóm 1”, một lớp học chỉ được đánh số; quốc gia được nhắc như địa danh; sản phẩm do tổ chức tạo ra.

**Ranh giới:** bao tên đầy đủ được viết ra, kể cả địa danh/tên người bên trong. Không tự mở rộng tên viết tắt.

### 1.7. WORK — Tác phẩm, văn bản hoặc tài liệu có nhan đề/tên

**Nhận:** tác phẩm văn học/nghệ thuật, sách, bài viết, văn kiện, văn bản pháp luật được gọi bằng nhan đề/tên xác định.

- “Bình Ngô đại cáo”, “Truyện Kiều”, “Tuyên ngôn Độc lập”.
- “Hiến pháp năm 2013” nếu đây là tên văn bản đang được đề cập.

**Loại:** “bài thơ”, “cuốn sách này”, “Bài 2”, “Chương 3”; đoạn trích nội dung tác phẩm; heading “Hàm số bậc hai” chỉ là tên chủ đề trừ khi có bằng chứng nó là nhan đề một tác phẩm/tài liệu.

**Phân biệt:** tên một bài thơ đồng thời là tên nhân vật có thể là `WORK` hoặc `PERSON` tùy lần xuất hiện. Trong “Tác phẩm Lão Hạc…” lấy `WORK`; trong “Lão Hạc bán chó…” lấy `PERSON`.

### 1.8. EVENT — Sự kiện lịch sử/xã hội có tên

**Nhận:** sự kiện, chiến dịch, cuộc cách mạng, hội nghị được gọi bằng một tên xác định.

- “Cách mạng tháng Tám”, “chiến dịch Điện Biên Phủ”, “Hội nghị Genève”.

**Loại:** ngày tháng riêng, “chiến tranh”, “cuộc họp”, một sự việc kể ngẫu nhiên trong bài toán. Từ “chiến tranh” dùng như khái niệm được giảng có thể là `CONCEPT`, nhưng không được tự coi mọi lần dùng là thuật ngữ.

**Ranh giới:** bao loại sự kiện khi là phần của tên. “Năm 1945, Cách mạng tháng Tám…” → không gán năm 1945; chỉ lấy tên sự kiện. Không bổ sung năm/địa danh ngoài span để làm rõ.

### 1.9. PRODUCT — Sản phẩm, phần mềm hoặc công cụ có tên riêng

**Nhận:** tên thương mại/tên riêng của phần mềm, nền tảng, thiết bị hoặc công cụ.

- “GeoGebra”, “Scratch”, “Microsoft Excel” khi đang nói về phần mềm.
- Tên model thiết bị được văn bản trình bày như sản phẩm cụ thể.

**Loại:** “máy tính”, “kính hiển vi”, “phần mềm” chung; tên công ty khi chủ thể là công ty; mã phiên bản đứng riêng.

Ngôn ngữ lập trình như “Python” được gán `CONCEPT` khi đang nói về ngôn ngữ, `PRODUCT` khi đoạn rõ ràng nói về một bản phân phối/sản phẩm cụ thể. Không mặc định mọi từ tiếng Anh viết hoa là sản phẩm.

## 2. Quyết định khi mentions cạnh tranh

Áp dụng theo thứ tự, trong cùng lần xuất hiện:

1. Xác định nghĩa của toàn biểu thức trong câu. Nhãn không được quyết định chỉ bằng viết hoa hoặc một token bên trong.
2. Nếu là tên đầy đủ của tổ chức/tác phẩm/sự kiện/sản phẩm, gán toàn tên với nhãn tương ứng, bỏ mentions lồng bên trong.
3. Nếu là tên quy tắc/định luật/định lý, dùng `NAMED_PRINCIPLE`, kể cả chứa tên người.
4. Nếu là tên cách làm/kỹ thuật/thuật toán, dùng `METHOD`.
5. Nếu là tên người/địa danh độc lập, dùng `PERSON`/`LOCATION`.
6. Nếu là thuật ngữ chuyên môn và chưa thuộc các trường hợp trên, dùng `CONCEPT`.
7. Nếu không đủ bằng chứng phân biệt hai nghĩa thực sự khả dĩ, issue `AMBIGUOUS_LABEL`; không ép vào `CONCEPT` như nhãn dự phòng.

## 3. Roles: 10 nhãn

Role biểu diễn chức năng của một đoạn. Evidence phải chứa nội dung thực hiện chức năng; heading đơn độc không đủ. Một role có thể dùng nhiều spans thuộc nhiều target elements, theo thứ tự nguồn. Không ghép các đoạn ở xa chỉ vì cùng nhãn. Trong cùng element, evidence của một role phải là một khoảng liên tục; nếu hai khoảng tách nhau bởi nội dung vai trò khác, tách thành hai roles.

| Nhãn | Điều kiện nhận | Điều kiện loại / ranh giới |
|---|---|---|
| `DEFINITION` | Nêu nghĩa, tiêu chí nhận diện hoặc quy ước định nghĩa một khái niệm | Câu có “là” nhưng chỉ kể thông tin, ví dụ “An là học sinh”, không đủ; giữ toàn mệnh đề định nghĩa và điều kiện |
| `THEOREM` | Phát biểu một định lý, định luật, nguyên lý, tính chất tổng quát hoặc quy tắc được trình bày như mệnh đề áp dụng chung | Chỉ nhắc tên không đủ; không gán mọi sự thật khoa học; phải có chức năng phát biểu quy tắc/tính chất, giữ giả thiết và kết luận |
| `PROOF` | Chuỗi lập luận nhằm chứng minh mệnh đề, với bước suy ra và/hoặc kết luận chứng minh | Đề “hãy chứng minh” là bài tập; nhãn “Chứng minh” trống không đủ; giải tính toán thông thường là `ANSWER` |
| `EXAMPLE` | Trường hợp cụ thể được tác giả đưa ra để minh họa khái niệm/cách làm, gồm cả ví dụ đã giải | “Ví dụ” chỉ nằm trong câu dẫn “xem ví dụ sau” không đủ; ví dụ chỉ giao nhiệm vụ chưa có minh họa/lời giải được xem là `EXERCISE` |
| `EXERCISE` | Nhiệm vụ học sinh phải thực hiện: tính, chứng minh, phân tích, chọn đáp án, thí nghiệm, điền chỗ trống… | Không tự giải; giữ dữ kiện, các ý phụ và lựa chọn trả lời thuộc bài; câu trích dẫn có mệnh lệnh không tự thành bài tập |
| `QUESTION` | Câu hỏi trực tiếp phục vụ hỏi–đáp/thảo luận hoặc gợi mở, không được đặt trong một khối bài tập được giao | Dấu hỏi trong văn trích/tự sự không đủ; câu hỏi có số bài/đề/nhiệm vụ học sinh rõ ràng ưu tiên `EXERCISE` |
| `ANSWER` | Câu/đoạn trả lời hoặc lời giải cho câu hỏi/bài tập xác định được từ target/context | Không tạo đáp án; bảng đáp án phải có ngữ cảnh xác định; chứng minh hoàn chỉnh ưu tiên `PROOF` |
| `PROCEDURE` | Các bước/hướng dẫn thực hiện một phương pháp hoặc hoạt động có thứ tự/thao tác rõ | Tên phương pháp đơn lẻ không đủ; nhiệm vụ yêu cầu học sinh xây dựng/thực hiện quy trình vẫn là `EXERCISE`; ví dụ đã giải ưu tiên `EXAMPLE` |
| `WARNING` | Cảnh báo an toàn, điều kiện cấm, sai lầm dễ mắc hoặc cách làm sai cần tránh | Marker “Chú ý” không tự động là cảnh báo; giữ nguyên phủ định và điều kiện; có thể một câu |
| `NOTE` | Ghi chú bổ sung, lưu ý phạm vi/ngoại lệ hoặc thông tin phụ được thể hiện là ghi chú | Không là nhãn mặc định cho văn bản thường; nếu thực chất là cảnh báo dùng `WARNING`; heading “Lưu ý” trống không đủ |

### 3.1. Cây quyết định cho vai trò chính

Áp dụng trên từng vùng chức năng có thể tách được:

1. Trước tiên xác định khung của toàn khối: có marker “Ví dụ”/“Example” hoặc context nguồn rõ là ví dụ minh họa, kèm nội dung minh họa thực tế hay phần giải được trình bày sẵn? → `EXAMPLE` cho toàn khối hoàn chỉnh, **kể cả đề bên trong có chữ “Tính”, “Hãy”, “Chứng minh”**; không gán roles lồng bên trong. Không áp dụng bước giao bài cho riêng câu đề ở trong khối này.
2. Nếu không thỏa bước 1, nội dung thực sự giao nhiệm vụ cho học sinh? → `EXERCISE`. “Ví dụ: Tính 2 + 3.” không có minh họa/lời giải vẫn là `EXERCISE`. Bài có nhãn “Bài 1” và đáp án riêng vẫn tách `EXERCISE` + `ANSWER`; việc có đáp án không tự đổi một bài tập thành ví dụ.
3. Đây là lập luận thực sự chứng minh một mệnh đề? → `PROOF`.
4. Đây là lời giải/trả lời cho một nhiệm vụ xác định? → `ANSWER`, trừ trường hợp 1/3.
5. Đây là định nghĩa thuật ngữ? → `DEFINITION`.
6. Đây là phát biểu quy tắc/định luật/tính chất tổng quát được trình bày như vậy? → `THEOREM`.
7. Đây là hướng dẫn thao tác có thể thực hiện theo? → `PROCEDURE`.
8. Đây là cảnh báo hoặc lưu ý bổ sung có bằng chứng? → `WARNING`/`NOTE`.
9. Đây là câu hỏi gợi mở/hỏi–đáp thực sự? → `QUESTION`.
10. Còn lại: không có role. Văn bản giải thích thông thường không phải lúc nào cũng cần nhãn.

Nếu một paragraph có hai câu độc lập, câu đầu định nghĩa và câu sau cho ví dụ, tách hai roles. Nếu phải cắt giữa điều kiện và kết luận để có hai roles thì không tách; chọn chức năng chính theo quy tắc trên. Trường hợp vẫn không quyết định được → `AMBIGUOUS_ROLE`.

### 3.1a. Phân biệt DEFINITION và THEOREM

Áp dụng sau khi đã xác định đoạn không nằm trong một khối ví dụ/bài tập được ưu tiên ở trên:

- `DEFINITION` thiết lập nghĩa hoặc tiêu chí gọi một đối tượng bằng một tên. Các mẫu “X được gọi là Y”, “X là Y có đặc điểm Z”, “Ta quy ước/gọi …” là dấu hiệu định nghĩa khi thực sự đang đặt tên/giải nghĩa.
- `THEOREM` phát biểu quan hệ, tính chất, kết quả hay dấu hiệu suy ra cho đối tượng đã xác định. Một mệnh đề vẫn là theorem khi có từ “là”; không phân loại chỉ bằng động từ nối.
- “khi và chỉ khi” không tự quyết định nhãn: có thể là tiêu chí định nghĩa hoặc một định lý tương đương. Dùng chức năng đặt nghĩa so với khẳng định tính chất và framing nguồn. Nếu vẫn không phân biệt được, issue `AMBIGUOUS_ROLE`.
- Marker “Định nghĩa”/“Định lý” đứng một mình không đủ. Nếu marker và chức năng phần thân thực sự mâu thuẫn, không chọn nhãn bằng vị trí ưu tiên; ghi `AMBIGUOUS_ROLE`. Một lỗi kiến thức khoa học đơn thuần không đồng nghĩa với xung đột chức năng này.

| Cặp sát ranh giới | Nhãn bắt buộc | Lý do |
|---|---|---|
| “Tam giác vuông là tam giác có một góc vuông.” | `DEFINITION` | Giải nghĩa tên loại tam giác |
| “Tính chất: Trong tam giác vuông, bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông.” | `THEOREM` | Quan hệ giữa các cạnh của đối tượng đã được xác định |
| “Số chẵn là số nguyên chia hết cho 2.” | `DEFINITION` | Tiêu chí gọi tên số chẵn |
| “Tính chất: Tổng hai số chẵn là số chẵn.” | `THEOREM` | Tính chất phép cộng, dù có từ “là” |
| “Định nghĩa: Tổng hai số chẵn là số chẵn.” | issue `AMBIGUOUS_ROLE` | Marker nói định nghĩa nhưng phần thân không định nghĩa thuật ngữ nào |

### 3.2. Ranh giới role

- Bắt đầu từ marker liền nội dung nếu có, ví dụ “Định nghĩa: …”, “Bài 2. …”.
- Kết thúc ở hết nội dung chức năng, gồm dấu câu cuối; bỏ khoảng trắng ngoài cùng.
- Nếu marker là một element riêng và nội dung ở element kế tiếp, có thể cùng một role với hai evidence spans khi cả hai đều thuộc target.
- Nếu marker chỉ ở context, dùng nó để hiểu role nhưng evidence chỉ gồm phần thân trong target.
- Với bài nhiều ý, giữ cùng một role nếu chúng thuộc cùng đề/dữ kiện và nằm trong target; bài độc lập tiếp theo là role khác.
- Với lời giải có nhiều bước, giữ một role xuyên các elements liên tiếp nếu không chen vai trò khác.
- Phần role bị cắt, thiếu giả thiết/lựa chọn/kết luận cần thiết: đánh dấu issue và không xuất role chưa đủ; các mentions rõ trong phần còn lại vẫn được giữ.
- Không bỏ các từ “không”, “chỉ khi”, “với điều kiện”, đơn vị hoặc giả thiết khi chọn span.

## 4. Links: 5 nhãn tùy chọn

Chỉ hoạt động khi `tasks` chứa `LINKS`. Liên kết chỉ được tạo giữa annotations đã có trong cùng output; evidence thuộc target. V1 không tạo liên kết xuyên target hoặc xuyên tài liệu.

| Nhãn | Kiểu hai đầu | Hướng | Bằng chứng đủ |
|---|---|---|---|
| `ALIAS_OF` | mention → mention | mention xuất hiện sau → mention xuất hiện trước | “A, còn gọi là B”, “A (viết tắt B)” thể hiện đồng chỉ; không suy ra từ trí nhớ |
| `TRANSLATION_OF` | mention → mention | mention xuất hiện sau → mention xuất hiện trước | Văn bản trực tiếp đặt hai tên Việt–Anh tương đương, ví dụ thuật ngữ và bản dịch trong ngoặc |
| `ANSWER_TO` | role `ANSWER` → role `EXERCISE`/`QUESTION` | lời giải → đề/câu hỏi | Cặp hỏi–đáp rõ hoặc số bài/nhãn liên kết không mơ hồ |
| `EXAMPLE_OF` | role `EXAMPLE` → mention `CONCEPT`/`METHOD`/`NAMED_PRINCIPLE` | ví dụ → kiến thức được minh họa | Có tham chiếu trực tiếp “ví dụ về/cho/minh họa …” chỉ đúng mention endpoint trong target |
| `PROOF_OF` | role `PROOF` → role `THEOREM` | chứng minh → mệnh đề | Có tên/số mệnh đề được viết ở cả phát biểu và tham chiếu chứng minh, ví dụ “Tính chất 1” và “Chứng minh tính chất 1” |

Alias/translation được dùng như liên kết tương đương cục bộ; hướng theo thứ tự nguồn chỉ để dữ liệu nhất quán, không xác định tên chuẩn toàn hệ thống. Chỉ một link cùng loại cho cùng cặp; không thêm chiều ngược. Không tạo link tới chính mình.

Nếu hai mentions khác ngôn ngữ nhưng nguồn thể hiện bản dịch, ưu tiên `TRANSLATION_OF`, không thêm `ALIAS_OF` cho cùng cặp. Viết tắt trong cùng ngôn ngữ dùng `ALIAS_OF`. Hai tên đứng cạnh nhau chưa đủ chứng minh tương đương. Liên kết không chắc có thể bỏ mà không issue nếu đơn giản không có bằng chứng; nếu target chứa quan hệ rõ nhưng bị cắt/hỏng khiến không xác định được endpoint, ghi issue phù hợp và `PARTIAL`.

**Không dùng vị trí liền kề làm bằng chứng link.** Một definition rồi một example không tự tạo `EXAMPLE_OF`; một theorem rồi một đoạn có marker “Chứng minh:” không tự tạo `PROOF_OF` trong phiên bản pilot này. Roles vẫn được gán bình thường. Với `ANSWER_TO`, yêu cầu số câu/bài trùng nhau xuất hiện trong evidence hoặc marker hỏi–đáp trực tiếp như “Q: … A: …” trong cùng target, không có câu hỏi cạnh tranh. Nếu có hai endpoint cùng số/marker khiến tham chiếu không duy nhất, không tạo link.

Evidence link phải chứa cả endpoint nguồn, endpoint đích và marker/tham chiếu thể hiện quan hệ; một hoặc nhiều source spans được phép. Không chỉ trích hai tên rồi để reviewer tự suy ra liên hệ. Trường hợp chỉ thiếu bằng chứng link thì `links=[]` vẫn có thể `COMPLETE`; nó không có nghĩa đã xác nhận hai đối tượng không liên quan.

Không có nhãn `PREREQUISITE_OF`, quan hệ nhân quả hay quan hệ tác giả trong bộ gán nhãn V1. Việc biết một kiến thức thường học trước kiến thức khác không cho phép suy ra quan hệ trong dataset này.
