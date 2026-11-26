# NetAct XML → Database

Author: [viet.pham_hoang@nokia.com](mailto:viet.pham_hoang@nokia.com)

**[English documentation / README_EN](README_EN.md)** · [Audit](AUDIT.md)

Giao diện và thông báo của ứng dụng hiện dùng tiếng Anh. Trong Database browser có thêm tab SQL workspace
để mở/chạy SQL script nhiều câu lệnh và export từng result set. CSV lấy từ snapshot của lần chạy, không chạy lại SQL.
Xem README_EN để biết chi tiết transaction, TTL kết quả và giới hạn triển khai.

Ứng dụng web tiếng Việt gộp hai bước **Nokia RAML20 XML → CSV → database**.
Dùng nguyên file `boda-nokiacmdataparser.jar` do người dùng cung cấp; phần nhập database
được viết lại từ quy trình trong script Python tham khảo. Không sao chép tài khoản hoặc mật khẩu cũ.

## Chạy bằng Python (không cần Docker)

Backend dùng **Python + FastAPI**, giao diện dùng HTML/CSS/JavaScript thuần, không dùng Bootstrap.
Yêu cầu Python 3.11+ và Java 11+ (`java -version`). Đã kiểm thử Python 3.12 và Java 21. MariaDB dùng đúng `mariadb` Connector/Python như script gốc;
MySQL dùng PyMySQL, PostgreSQL dùng psycopg.
Cài Python, Java và Git trước khi chạy; Java phải nằm trong PATH.

### Windows PowerShell

```powershell
git clone https://github.com/phamhviet/netact-db-tool.git
cd netact-db-tool
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Không cần kích hoạt virtualenv hay đổi ExecutionPolicy khi dùng trực tiếp `python.exe` như trên.

### Linux / macOS

Connector `mariadb==1.1.15` cần MariaDB Connector/C khi cài từ source. Trên Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y gcc python3-dev libmariadb-dev
```

Trên macOS: `brew install mariadb-connector-c pkg-config` và dùng compiler Xcode Command Line Tools.
Nếu dùng Windows, ưu tiên Python 3.12 x64 (đã kiểm tra có wheel). Nếu pip yêu cầu build,
cần MariaDB Connector/C và Visual C++ Build Tools phù hợp phiên bản Python.


```bash
git clone https://github.com/phamhviet/netact-db-tool.git
cd netact-db-tool
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Nếu đã tải ZIP từ GitHub, giải nén và mở terminal trong thư mục dự án rồi chạy từ bước tạo `.venv`.
Trên GitHub có thể tải bằng **Code → Download ZIP**. Không cần tải ZIP đính kèm trong chat.
Để tái lập đúng toàn bộ phiên bản đã kiểm thử (gồm công cụ test), dùng
`-m pip install -r requirements.lock` thay cho `requirements.txt`.
Dừng máy chủ bằng **Ctrl+C** sau khi tác vụ nhập dữ liệu hoàn tất.

Mở trình duyệt tại `http://localhost:8000`. Chọn XML từ máy tính, nhập loại DB,
host, port, database, username, password và TLS. Nhấn **Kiểm tra kết nối**, sau đó
**Chuyển đổi & nhập**. Xem tiến độ và số bảng/dòng đã lưu.

- **Database đã có:** bỏ chọn “Tạo database mới”; tài khoản cần quyền CONNECT, CREATE TABLE và INSERT.
- **Database mới:** chọn “Tạo database mới”; kiểm tra database tồn tại: có thì DROP rồi CREATE cùng tên, chưa có thì chỉ CREATE. PostgreSQL dùng database `postgres` để kết nối quản trị;
  MariaDB/MySQL kết nối máy chủ không chọn database trước, giống script gốc. **Nếu database đã tồn tại, toàn bộ dữ liệu trong đó sẽ bị xóa.**
- Tên database: chữ Latin, số, `_`, tối đa 63 ký tự, không bắt đầu bằng số.
- Host là địa chỉ **máy chủ web** truy cập được; không phải địa chỉ tương đối với máy đang mở trình duyệt.
- PostgreSQL mặc định 5432; MariaDB/MySQL 3306. Có thể sửa port.
- `verify-full` xác minh TLS bằng CA tin cậy của máy chủ. Chứng chỉ riêng cần cài CA trên máy chủ;
  không tắt xác minh để khắc phục lỗi chứng chỉ. PostgreSQL còn có `require` (mã hóa, không xác minh danh tính máy chủ).

## Lưu cấu hình 3 loại database trong .env

Sau khi cập nhật mã nguồn, cài lại dependencies: `python -m pip install -r requirements.txt`
(Windows có thể dùng `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`).

Tạo `.env` cạnh `README.md` bằng cách sao chép `.env.example` **nếu chưa có file .env**.
Trong PowerShell:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Mỗi loại DB có cấu hình riêng:

```dotenv
MARIADB_HOST=192.168.1.10
MARIADB_PORT=3306
MARIADB_USER=your_user
MARIADB_PASSWORD='your_password'
MARIADB_DATABASE=
MARIADB_SSL_MODE=disable

POSTGRES_HOST=192.168.1.20
POSTGRES_PORT=5432
POSTGRES_USER=your_user
POSTGRES_PASSWORD='your_password'
POSTGRES_DATABASE=
POSTGRES_SSL_MODE=disable

MYSQL_HOST=192.168.1.30
MYSQL_PORT=3306
MYSQL_USER=your_user
MYSQL_PASSWORD='your_password'
MYSQL_DATABASE=
MYSQL_SSL_MODE=disable
```

Đây là địa chỉ ví dụ; thay bằng IP/user/password thực tế. `DATABASE` để trống nếu muốn tự nhập tên trên web.
Đặt password trong dấu nháy đơn nếu có `#`, `$` hoặc khoảng trắng; Python không nội suy biến trong giá trị.
Khởi động lại ứng dụng sau khi sửa cấu hình, rồi tải lại trang.

- Khi chọn MariaDB/PostgreSQL/MySQL, cả form nhập XML và form duyệt DB tự điền host, port, user,
  TLS và tên database nếu đã cấu hình. Có thể sửa các ô trên web.
- Nếu có biến `*_PASSWORD` (kể cả giá trị rỗng), ô **Dùng mật khẩu trong .env** được chọn sẵn.
  Mật khẩu được dùng ở server; API chỉ trả cờ có mật khẩu, không trả mật khẩu xuống trình duyệt.
- Đổi host/port/user/TLS sẽ tắt sử dụng mật khẩu lưu sẵn. Muốn dùng kết nối khác, nhập mật khẩu riêng;
  không tự chuyển mật khẩu trong .env sang địa chỉ khác. Gõ mật khẩu riêng cũng tắt ô sử dụng .env.
- `.env` được Git và Docker build bỏ qua. Chỉ `.env.example` với giá trị mẫu được đưa lên GitHub.
  Không điền mật khẩu thật vào `.env.example`. Biến môi trường của tiến trình ưu tiên hơn file `.env`.
- Docker Compose đọc file `.env` bằng `env_file` (Compose 2.24+); nếu không có vẫn chạy và nhập thủ công.
  Sau khi thay `.env`, chạy lại `docker compose up -d --force-recreate` để cập nhật biến trong container.
  Không dùng `localhost` làm DB host trong container nếu DB nằm ở máy chủ; dùng host phù hợp.
- Cấu hình .env là dùng chung cho người được quyền truy cập ứng dụng này. Giữ phạm vi máy cá nhân/mạng tin cậy
  như phần triển khai; ngắt phiên duyệt không xóa cấu hình .env ở server.

## Chạy bằng Docker

```bash
docker compose up --build -d
```

Mở `http://localhost:8000`. Nếu database chạy trên cùng máy Docker, nhập host
`host.docker.internal`, không dùng `localhost`. Compose chỉ mở cổng trên loopback của máy chủ.
Dừng bằng `docker compose down`.

## Duyệt database và export CSV trên web

1. Chọn **Duyệt database** ở thanh bên (trên điện thoại cũng có nút chuyển mục).
2. Nhập loại database, host, port, tên database, username, password và TLS.
   Nhấn **Kết nối & xem bảng**. Không cần XML; database phải tồn tại sẵn.
3. Chọn schema, tìm tên bảng và nhấn bảng để xem dữ liệu. Nút **Làm mới** tải lại danh sách bảng.
4. Chọn 50/100/500/1.000 dòng mỗi trang, dùng **Trước/Tiếp** để xem tiếp.
5. Nhấn **Export CSV** để tải **toàn bộ bảng đang chọn**, không chỉ trang đang xem.
6. Nhấn **Ngắt kết nối** để xóa phiên duyệt; muốn đổi thông tin kết nối, ngắt rồi nhập lại.

- Chức năng duyệt chỉ đọc, tài khoản DB cần quyền xem schema/bảng và SELECT.
  Không có chức năng chạy SQL tùy ý, sửa hoặc xóa dữ liệu.
- Tên bảng/schema được kiểm tra với metadata DB; tên có khoảng trắng hoặc dấu ngoặc kép vẫn được xử lý.
- Mật khẩu nhập tay được giữ trong bộ nhớ của tab, gửi trong body POST để đọc dữ liệu,
  không lưu vào localStorage/sessionStorage, URL hay cấu hình máy chủ. Tải lại trang sẽ phải kết nối lại.
  Máy chủ mở và đóng kết nối cho từng yêu cầu, không lưu phiên chứa mật khẩu.
- Nếu bảng có khóa chính, trang dữ liệu được sắp xếp theo khóa chính. Bảng không có khóa chính
  không đảm bảo thứ tự cố định; thay đổi dữ liệu đồng thời có thể khiến dòng lặp/thiếu giữa các trang.
  Không chạy COUNT toàn bảng khi mở trang để tránh truy vấn nặng trên bảng lớn.
- Ô dài chỉ hiển thị 500 ký tự trên web; export giữ giá trị đầy đủ.
  CSV là UTF-8 BOM, có header và quoting dấu phẩy/ngoặc kép/xuống dòng.
  SQL NULL thành ô rỗng trong CSV; dữ liệu binary thành chuỗi `base64:...`.
- Export dùng cursor theo lô 1.000 dòng và browser tải trực tiếp, không gom toàn bộ bảng vào RAM.
  Không đặt giới hạn số dòng export. Phiên duyệt và export là độc lập;
  ngắt kết nối trên web không hủy download đã bắt đầu. Nếu mất kết nối DB/mạng giữa chừng,
  file CSV có thể bị thiếu; cần tải lại. Nếu bị từ chối trước khi tải, lỗi hiển thị ở tab mới.
- Dùng HTTPS khi truy cập qua mạng; phạm vi một người dùng/mạng tin cậy như phần triển khai bên dưới.

![Giao diện duyệt database](docs/database-explorer.png)

## Chẩn đoán tác vụ thất bại

Thông báo lỗi nhập dữ liệu cho biết giai đoạn chuyển XML/CSV hoặc nhập dữ liệu,
loại exception và mã lỗi DB. Phần chuẩn bị database tách rõ **Kết nối máy chủ**, **Kiểm tra tồn tại**, **DROP DATABASE**,
**CREATE DATABASE** và **Mở database đích**; nếu DROP thành công nhưng CREATE lỗi, thông báo nói rõ trạng thái đó.

Với MariaDB/MySQL, kết nối để tạo database không chọn `information_schema` hay database đích trước,
giống `mariadb.connect(user=..., password=..., host=..., port=...)` trong script gốc.
Nút **Kiểm tra kết nối** chỉ chạy SELECT và hiển thị tài khoản đã xác thực từ `CURRENT_USER()` (`user@host`),
không thực hiện DROP/CREATE. Kết nối thành công không đồng nghĩa đã có quyền xóa/tạo database.

Nếu lỗi `1044`, nhấn **Chẩn đoán DB** ở form kết nối (không cần upload XML):
- Chỉ chạy SELECT/SHOW; không chạy DROP/CREATE, không tạo database thử.
- Hiển thị host/port đang dùng, CURRENT_USER, USER/session_user, hostname/port/version của server,
  database nhìn thấy và chế độ tạo mới/tái sử dụng đang chọn. MariaDB/MySQL có CURRENT_ROLE và
  phần GRANT theo phạm vi; toàn bộ đoạn tài khoản/authentication/hash sau TO được loại bỏ.
- Lỗi Access denied tiêu chuẩn giữ phản hồi gốc của DB về tài khoản/database, không đưa câu SQL,
  giá trị tham số hoặc mật khẩu vào kết quả. Không tự kết luận tài khoản root thiếu quyền chỉ từ mã lỗi.
- Khi bỏ chọn **Tạo database mới**, import không gửi bất kỳ lệnh DROP DATABASE/CREATE DATABASE nào.
  Khi bật, lệnh CREATE không thêm CHARACTER SET/COLLATE để khớp script gốc; DB dùng mặc định của server.

Đối chiếu lệnh bị server từ chối:
- `DROP DATABASE`: tài khoản cần quyền DROP trên đúng database.
- `CREATE DATABASE`: tài khoản cần quyền CREATE trên đúng database.
- `Mở database`: kiểm tra tên database và quyền truy cập.

Dùng cùng host, port, username, password và tên database như script đang chạy được.
Không cần cấp quyền toàn cục: có thể cấp DROP/CREATE và quyền bảng trong phạm vi database đích.
Nếu thiếu quyền, nhờ người quản trị DB cấp quyền đúng phạm vi hoặc dùng tài khoản mà script gốc đang dùng.
Không gửi mật khẩu hoặc toàn bộ SHOW GRANTS (có thể chứa thông tin xác thực) vào chat.
Log chỉ ghi mã tác vụ, giai đoạn, thao tác, loại exception và mã lỗi; không ghi mật khẩu/SQL/dữ liệu XML.

### Đối chiếu với script Python gốc đang chạy được

MariaDB của bản web dùng `mariadb==1.1.15` (SQLAlchemy driver `mariadbconnector`),
đúng loại connector `mariadb` và placeholder `?` của script gốc; MySQL vẫn dùng PyMySQL.
Sau khi cập nhật cần cài lại `requirements.txt` và khởi động lại Python.

Nếu cần đối chiếu cấu hình thực tế, chạy trên **chính máy đang chạy được script gốc**:

```powershell
.\.venv\Scripts\python.exe tools\compare_db_config.py "D:\duong-dan\netact_mariadb_import.py"
```

Công cụ chỉ phân tích các hằng username/password/host/port/database_name trong script,
**không import/chạy script và không tạo/xóa DB**. Nó kết nối chỉ đọc bằng `mariadb.connect` trực tiếp như script gốc, rồi bằng đường kết nối web với .env,
in `settings_match` cho host/port/user/password (chỉ true/false, không in mật khẩu),
server/tài khoản thực tế và phạm vi quyền đã bỏ thông tin xác thực/hash.
Dùng kết quả để xác định hai đường chạy có thật sự cùng cấu hình/server/tài khoản hay không.
Nếu script lấy cấu hình qua hàm/biến phức tạp thay vì hằng literal, công cụ sẽ báo không đọc được cấu hình.

## Hành vi nhập và xử lý lỗi

- Hỗ trợ MariaDB, PostgreSQL, MySQL qua SQLAlchemy. **MariaDB 10.11 (native Connector/Python), 11.4 (bản trước) và PostgreSQL 17 đã có kiểm thử tích hợp**;
  MySQL dùng driver PyMySQL riêng nhưng chưa được kiểm thử với máy chủ MySQL riêng.
- Luồng import CSV bám theo script Python gốc: đọc header; thử encoding theo thứ tự UTF-8 BOM,
  CP1252, Latin-1, ISO-8859-1; bỏ qua file rỗng; quét toàn bộ dữ liệu để xác định độ dài lớn nhất từng cột.
- Cột có độ dài lớn nhất từ 0–255 ký tự dùng `VARCHAR(max(1, độ_dài))`; trên 255 dùng `TEXT`.
  Giữ ID dạng chuỗi, số 0 đầu và chuỗi `null`. MariaDB/MySQL TEXT vẫn có giới hạn 65.535 byte/giá trị
  như script gốc; dòng vượt giới hạn sẽ được ghi lỗi và bỏ qua, không tự đổi sang kiểu khác.
- MariaDB/MySQL: tên bảng là tên file cắt 64 **ký tự**; cột được strip/cắt 64 ký tự, cột rỗng dùng
  `unknown_column`, tên trùng được thêm `_1`, `_2`... theo kiểm tra phân biệt hoa/thường của script.
  Trùng tên chỉ khác hoa/thường có thể khiến MariaDB từ chối tạo bảng; tác vụ ghi lỗi và chuyển bảng tiếp theo.
  Tên bảng sau cắt có thể trùng và tái sử dụng cùng bảng. PostgreSQL được điều chỉnh theo giới hạn 63 byte UTF-8.
- Dùng `CREATE TABLE IF NOT EXISTS`, quote tên bảng/cột để hỗ trợ từ khóa FUNCTION/RANK/VERSION.
  Nếu bảng có sẵn thì giữ nguyên cấu trúc và insert theo vị trí cột giống script gốc.
- Thiếu cột được điền chuỗi rỗng; thừa cột bị cắt; dòng trắng được nhập thành các giá trị rỗng.
  Insert từng dòng giống script: lỗi tạo bảng thì bỏ bảng đó, lỗi insert thì bỏ dòng đó và tiếp tục.
  Không rollback/xóa các bảng nhập thành công khi gặp những lỗi này. PostgreSQL dùng savepoint
  để lỗi một dòng không làm hỏng toàn bộ transaction. Commit sau khi duyệt các file CSV.
- Kết quả hiển thị số dòng thành công, dòng lỗi, bảng lỗi và tối đa 20 chi tiết lỗi mỗi file,
  không chứa giá trị dữ liệu/SQL/mật khẩu. Nếu có lỗi, trạng thái là **Hoàn tất có lỗi**, không báo thành công toàn bộ.
- Chỉ có một tùy chọn **Tạo database mới**: bật thì kiểm tra database tồn tại; có mới chạy `DROP DATABASE IF EXISTS` rồi `CREATE DATABASE`
  cùng tên, chưa có thì chỉ chạy `CREATE DATABASE`; tắt thì dùng database hiện có và giữ dữ liệu. Không có ô reset/xác nhận tên riêng.
  **Khi bật, toàn bộ bảng/dữ liệu của database trùng tên sẽ bị xóa.** Tài khoản cần quyền DROP/CREATE DATABASE.
  Kiểm tra kết nối và duyệt database không bao giờ thực hiện DROP/CREATE.
- Lỗi đọc file/CSV, commit, mất kết nối hoặc tiến trình bị dừng có thể làm tác vụ dừng và còn dữ liệu nhập dở,
  như khi script bị gián đoạn. Không chạy đồng thời tác vụ khác sửa cùng database trong lúc import.
- Chỉ nhận một tác vụ tại một thời điểm; chạy **1 worker**, không dùng nhiều replica.
  Tiến độ phản ánh các giai đoạn và số bảng đã xử lý, không phải phần trăm byte chính xác.
- Không giới hạn dung lượng XML ở giao diện hoặc backend; không còn dùng `MAX_UPLOAD_MB`.
  File được sao chép theo khối 1 MB, không đọc toàn bộ file upload vào RAM.
  Multipart có thể spool xuống ổ đĩa trước đó: cần đủ ổ đĩa tạm cho multipart, XML gốc, XML chuẩn hóa và CSV.
  Nếu dùng Nginx, đặt `client_max_body_size 0;` và tăng timeout proxy phù hợp cho upload dài;
  các proxy/gateway khác có thể có giới hạn riêng cần cấu hình ở đó.
- Không đặt timeout chuyển đổi JAR hoặc ép heap ở 1 GB. JVM chọn heap theo RAM khả dụng.
  Với dữ liệu lớn, có thể cấp heap bằng `JAVA_TOOL_OPTIONS`, ví dụ `-Xmx8g` trên máy đủ RAM
  (PowerShell: `$env:JAVA_TOOL_OPTIONS="-Xmx8g"`; Linux: `export JAVA_TOOL_OPTIONS="-Xmx8g"`).
  Không giới hạn dung lượng upload không đồng nghĩa RAM/ổ đĩa vô hạn; khả năng xử lý vẫn phụ thuộc
  tài nguyên máy chủ và parser JAR. Chưa benchmark bằng XML thực tế dung lượng nhiều GB.
  XML phải đúng Nokia RAML20. Hỗ trợ khai báo DOCTYPE không chứa entity: ứng dụng chuẩn hóa XML theo luồng
  sang UTF-8 và loại DOCTYPE trước khi gọi JAR, không tải DTD ngoài. File upload gốc không bị sửa.
  Khai báo entity bị từ chối; cần xuất XML với giá trị trực tiếp. Lỗi cú pháp được báo theo dòng/cột của file gốc.
  Đây không phải bộ chuyển đổi cho mọi cấu trúc XML. File tạm có tên `input.xml`, nên cột FILENAME của CSV mang tên đó.
- XML, CSV và log JAR tạm bị xóa sau tác vụ; không ghi mật khẩu nhập trên form vào file cấu hình, log hoặc kết quả.
  Mật khẩu dùng trong bộ nhớ của tiến trình để kết nối và được xóa khỏi form sau khi nhận tác vụ.
  Metadata kết quả giữ tối đa 100 tác vụ/24 giờ trong bộ nhớ, mất khi khởi động lại.
  Session trình duyệt chỉ lưu mã tác vụ để tiếp tục theo dõi khi tải lại trang.

## Phạm vi triển khai

Phiên bản này dành cho **một người dùng trên máy cá nhân/mạng nội bộ tin cậy**, chưa có đăng nhập.
Mặc định bind loopback. Nếu mở cho nhiều người hoặc Internet, cần reverse proxy HTTPS, xác thực,
phân quyền, giới hạn upload, giới hạn tài nguyên và danh sách host database được phép trước khi triển khai.
API kết nối database sử dụng địa chỉ do người dùng nhập, nên chỉ cho người tin cậy truy cập.
Không gửi mật khẩu qua HTTP trên mạng công cộng. API chặn POST khác Origin; không thay thế xác thực.
Không dùng `--reload` khi có tác vụ đang chạy: khởi động lại sẽ mất trạng thái.

## Kiểm thử

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Mặc định hai kiểm thử database được skip. Để chạy tích hợp trên **database kiểm thử riêng**, không dùng DB sản xuất:

```bash
docker run -d --name netact-test-pg -e POSTGRES_PASSWORD=local-test-only -p 127.0.0.1:55432:5432 postgres:17
docker run -d --name netact-test-maria -e MARIADB_ROOT_PASSWORD=local-test-only -p 127.0.0.1:53306:3306 mariadb:11.4
# Chờ hai DB khởi động và nhận kết nối rồi chạy:
NETACT_TEST_DATABASES=1 python -m pytest -q
docker rm -f netact-test-pg netact-test-maria
```

Kiểm thử chạy JAR thật, upload API, tạo DB tạm, đọc lại giá trị tiếng Việt/ID `001`,
kiểm tra tái sử dụng bảng, kiểu VARCHAR/TEXT, dòng thiếu/thừa/trắng, encoding, bảng 400 cột,
lỗi tạo bảng/insert vẫn tiếp tục, tên file cắt trùng và tạo mới/xóa-tạo-lại database kiểm thử. Kiểm thử database còn xác minh danh sách bảng,
phân trang, tên bảng có dấu ngoặc kép, NULL và export 1.205 dòng qua nhiều lô, giữ nguyên giá trị CSV. Chỉ DB tên ngẫu nhiên do test tạo mới bị xóa.
Chưa có XML thực tế của người dùng để xác nhận các class/list đặc thù hoặc hiệu năng file lớn.

## Thành phần bên thứ ba

`vendor/boda-nokiacmdataparser.jar` do người dùng cung cấp, CLI ghi tác giả Bodastage Solutions (2019).
Giữ nguyên binary; kiểm tra điều khoản phân phối của công cụ trước khi phát hành sản phẩm ra ngoài.
