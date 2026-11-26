import csv
import errno
import logging

from sqlalchemy.exc import DBAPIError

logger = logging.getLogger('netact')
STAGES = {'xml_to_csv': 'Chuyển XML sang CSV', 'database_connect': 'Kết nối/tạo database',
          'csv_to_database': 'Tạo bảng/nhập CSV'}
DATABASE_ERRORS = {
    '1045': 'Sai tài khoản/mật khẩu hoặc tài khoản không được phép kết nối từ máy chủ web.',
    '1044': 'Tài khoản không có quyền truy cập hoặc tạo database này.',
    '1049': 'Database chưa tồn tại. Tạo trước hoặc chọn Tạo database mới.',
    '1007': 'Database đã tồn tại hoặc đang được tạo bởi tác vụ khác. Kiểm tra tên đích và quyền DROP/CREATE.',
    '1142': 'Tài khoản thiếu quyền thực hiện thao tác trên bảng (CREATE/INSERT/SELECT/DROP).',
    '1050': 'Bảng đã tồn tại trong database đích.',
    '1118': 'Bảng vượt giới hạn kích thước một dòng của MariaDB/MySQL.',
    '1117': 'Bảng vượt số cột mà database hỗ trợ.',
    '1406': 'Giá trị vượt dung lượng kiểu cột đích; MariaDB/MySQL TEXT tối đa 65.535 byte.',
    '1366': 'Giá trị hoặc bảng mã không tương thích với cột đích. Kiểm tra UTF-8/utf8mb4.',
    '2003': 'Không tới được máy chủ database. Kiểm tra host, port, firewall và quyền truy cập mạng.',
    '2005': 'Không phân giải được hostname database.',
    '2006': 'Máy chủ database đã đóng kết nối. Kiểm tra log DB, timeout và max_allowed_packet.',
    '2013': 'Mất kết nối trong khi truy vấn. Kiểm tra log DB, mạng và timeout.',
    '1153': 'Gói dữ liệu vượt max_allowed_packet của database.',
    '28P01': 'Sai thông tin xác thực PostgreSQL.',
    '28000': 'PostgreSQL từ chối xác thực. Kiểm tra tài khoản và pg_hba.conf.',
    '3D000': 'Database chưa tồn tại. Tạo trước hoặc chọn Tạo database mới.',
    '42501': 'Tài khoản PostgreSQL thiếu quyền trên database/schema/bảng.',
    '42P04': 'Database đã tồn tại hoặc đang được tạo bởi tác vụ khác. Kiểm tra tên đích và quyền DROP/CREATE.',
    '42P07': 'Bảng đã tồn tại trong schema đích.',
    '54011': 'Bảng vượt số cột PostgreSQL hỗ trợ.',
    '54000': 'Dữ liệu vượt giới hạn của PostgreSQL.',
    '22021': 'Dữ liệu chứa ký tự hoặc encoding PostgreSQL không chấp nhận.',
    '22001': 'Giá trị vượt dung lượng kiểu cột đích.',
    '53100': 'Máy chủ PostgreSQL hết dung lượng ổ đĩa.',
    '53200': 'Máy chủ PostgreSQL không đủ bộ nhớ.',
    '53300': 'Máy chủ PostgreSQL đã hết kết nối khả dụng.',
    '08001': 'Không thiết lập được kết nối PostgreSQL.',
    '08006': 'Mất kết nối PostgreSQL trong quá trình xử lý.',
}


class DatabaseOperationError(Exception):
    def __init__(self, operation, original, database, state='unchanged'):
        super().__init__('Database operation failed')
        self.operation = operation
        self.original = original
        self.database = database
        self.state = state


def failure_details(exc, stage, job_id, log_error=True):
    operation = exc.operation if isinstance(exc, DatabaseOperationError) else None
    database = exc.database if isinstance(exc, DatabaseOperationError) else None
    state = exc.state if isinstance(exc, DatabaseOperationError) else None
    if isinstance(exc, DatabaseOperationError):
        exc = exc.original
    code = None
    reason = 'Lỗi chưa phân loại. Gửi giai đoạn và loại lỗi bên dưới để kiểm tra tiếp.'
    if isinstance(exc, DBAPIError):
        original = exc.orig
        sqlstate = getattr(original, 'sqlstate', None)
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isascii() and sqlstate.isalnum():
            code = sqlstate
        elif original.args and isinstance(original.args[0], int):
            code = str(original.args[0])
        reason = DATABASE_ERRORS.get(code, 'Database từ chối thao tác hoặc kết nối bị lỗi. Kiểm tra log database và quyền tài khoản.')
    elif isinstance(exc, OSError):
        code = f'OS-{exc.errno}' if isinstance(exc.errno, int) else None
        reason = {errno.ENOSPC: 'Máy chủ web hết dung lượng ổ đĩa tạm.',
                  errno.EACCES: 'Máy chủ web không có quyền đọc/ghi file tạm hoặc chạy Java.',
                  errno.ENOMEM: 'Máy chủ web không đủ bộ nhớ.'}.get(exc.errno, 'Không đọc/ghi được file hoặc không khởi chạy được Java.')
    elif isinstance(exc, csv.Error):
        reason = 'Không đọc được CSV. Có thể một ô vượt giới hạn đọc CSV hoặc định dạng CSV không hợp lệ.'
    elif isinstance(exc, UnicodeError):
        reason = 'Không giải mã được dữ liệu. Kiểm tra encoding XML/CSV.'
    label = STAGES[stage]
    if operation:
        label = {'server_connect': 'Kết nối máy chủ database (không chọn database đích)',
                 'drop_database': f'DROP DATABASE [{database}]',
                 'create_database': f'CREATE DATABASE [{database}]',
                 'target_connect': f'Mở database [{database}]'}[operation]
    if code in ('1044', '42501') and operation:
        reason = {'drop_database': 'Tài khoản không có quyền DROP database này.',
                  'create_database': 'Tài khoản không có quyền CREATE database này.',
                  'target_connect': 'Tài khoản không có quyền truy cập database đích. Kiểm tra đúng tên database và tài khoản.',
                  'server_connect': 'Máy chủ từ chối truy cập. Kiểm tra tài khoản được cấp quyền theo host kết nối.'}[operation]
    suffix = 'Database mới tạo có thể vẫn còn; kiểm tra trước khi thử lại.'
    if state == 'dropped':
        suffix = 'Lệnh DROP đã thành công, nhưng lệnh CREATE chưa được xác nhận thành công.'
    elif state == 'created':
        suffix = 'Database đã được tạo lại, nhưng ứng dụng chưa mở được database đích.'
    elif operation == 'server_connect' or (state == 'unchanged' and operation in ('drop_database', 'target_connect') and code in ('1044', '1045', '42501', '28P01')):
        suffix = 'Ứng dụng chưa xóa/tạo lại database.'
    kind = type(exc).__name__
    # Never log str(exc), SQL, parameter values, credentials or XML contents.
    if log_error:
        logger.error('job=%s stage=%s operation=%s exception=%s code=%s',
                     job_id, stage, operation or 'none', kind, code or 'none')
    identifier = f'{kind}, mã {code}' if code else kind
    return {'stage': stage, 'operation': operation, 'error_code': code, 'error_type': kind,
            'message': f'{label} thất bại ({identifier}). {reason} {suffix}'}
