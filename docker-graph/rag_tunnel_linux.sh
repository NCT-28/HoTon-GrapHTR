#!/bin/bash

# --- Cấu hình ---
# Chỉ cần thay đổi các giá trị trong phần này
USER_NAME="toannc"      # Ubuntu server thường dùng root hoặc ubuntu
TUNNEL_NAME="RAG"
DOMAIN="rag.f1p.info"
LOCAL_PORT=8030
# -----------------

# Dừng script ngay lập tức nếu có lỗi
set -e
set -o pipefail

# --- Biến và Hằng số ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Detect OS
OS_TYPE=$(uname)

HOME_DIR=$(eval echo "~$USER_NAME")
if [ ! -d "$HOME_DIR" ]; then
    echo -e "${RED}[ERROR] Thư mục home cho người dùng '$USER_NAME' không tồn tại tại '$HOME_DIR'.${NC}"
    exit 1
fi

# Detect cloudflared binary — tự cài nếu chưa có
if command -v cloudflared &> /dev/null; then
    CLOUD_FLARED_BIN=$(command -v cloudflared)
elif [ -f "/usr/bin/cloudflared" ]; then
    CLOUD_FLARED_BIN="/usr/bin/cloudflared"
else
    echo -e "${YELLOW}[WARN] cloudflared chưa được cài. Đang tải và cài đặt...${NC}"
    ARCH=$(uname -m)
    if [[ "$ARCH" == "aarch64" ]]; then
        CF_PKG="cloudflared-linux-arm64.deb"
    else
        CF_PKG="cloudflared-linux-$(dpkg --print-architecture).deb"
    fi
    curl -Lo "/tmp/$CF_PKG" "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_PKG"
    dpkg -i "/tmp/$CF_PKG"
    rm -f "/tmp/$CF_PKG"
    CLOUD_FLARED_BIN=$(command -v cloudflared)
    echo -e "${GREEN}[INFO] Đã cài đặt cloudflared: $CLOUD_FLARED_BIN${NC}"
fi

CONFIG_DIR="$HOME_DIR/.cloudflared"
CERT_FILE="$CONFIG_DIR/cert.pem" # Dùng chung cert.pem cho tất cả các tunnel

# --- Hàm trợ giúp ---
info() { echo -e "${GREEN}[INFO] $1${NC}"; }
warn() { echo -e "${YELLOW}[WARN] $1${NC}"; }
error() { echo -e "${RED}[ERROR] $1${NC}" >&2; exit 1; }

# --- Bắt đầu Script ---
info "Bắt đầu script cài đặt Cloudflare Tunnel cho người dùng: $USER_NAME"
info "Hệ điều hành: $OS_TYPE"
info "Tên Tunnel: $TUNNEL_NAME"
info "Tên miền: $DOMAIN"
info "Cổng nội bộ: $LOCAL_PORT"

if [[ $EUID -ne 0 ]]; then
   error "Script này cần được chạy với quyền root. Vui lòng sử dụng: sudo $0"
fi

# Xử lý cert.pem — hỗ trợ server headless (không có browser)
if [ ! -f "$CERT_FILE" ]; then
  echo -e "${YELLOW}[WARN] Chưa có file xác thực: $CERT_FILE${NC}"
  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  Server không có browser. Chọn cách lấy cert.pem:           ║${NC}"
  echo -e "${YELLOW}║                                                              ║${NC}"
  echo -e "${YELLOW}║  1) Dừng — copy cert.pem từ Mac lên rồi chạy lại            ║${NC}"
  echo -e "${YELLOW}║     Mac: brew install cloudflared && cloudflared login       ║${NC}"
  echo -e "${YELLOW}║     Mac: scp ~/.cloudflared/cert.pem root@IP:$CERT_FILE     ║${NC}"
  echo -e "${YELLOW}║                                                              ║${NC}"
  echo -e "${YELLOW}║  2) Tiếp tục — đăng nhập qua URL hiện trong terminal SSH    ║${NC}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  read -r -p "Chọn (1/2): " _CHOICE
  if [[ "$_CHOICE" == "1" ]]; then
    echo "Chạy lại script sau khi đã copy cert.pem lên server."
    exit 0
  fi
  echo ""
  info "Đang chạy cloudflared login — copy URL bên dưới và mở trên trình duyệt..."
  sudo -u "$USER_NAME" "$CLOUD_FLARED_BIN" login
  [ ! -f "$CERT_FILE" ] && error "Đăng nhập thất bại. File cert.pem vẫn chưa tồn tại."
  info "Đăng nhập thành công! cert.pem đã được tạo."
fi

# Thư mục config được dùng chung, chỉ cần đảm bảo nó tồn tại
sudo -u "$USER_NAME" mkdir -p "$CONFIG_DIR"

info "Bước 1: Dọn dẹp tunnel '$TUNNEL_NAME' cũ nếu có"

if [[ "$OS_TYPE" == "Darwin" ]]; then
    # macOS Logic
    SERVICE_NAME="com.cloudflare.cloudflared.$TUNNEL_NAME"
    PLIST_PATH="/Library/LaunchDaemons/$SERVICE_NAME.plist"

    # Stop and unload service if exists
    if launchctl list | grep -q "$SERVICE_NAME"; then
        launchctl bootout system "$PLIST_PATH" 2>/dev/null || true
    fi
    rm -f "$PLIST_PATH" || true
else
    # Linux Logic
    SERVICE_NAME="cloudflared-$TUNNEL_NAME.service"
    # Chỉ dừng và xóa service liên quan đến tunnel này, không gỡ bỏ package cloudflared
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$SERVICE_NAME" || true
    systemctl daemon-reload
fi

info "Đã dọn dẹp service cũ (nếu có)."

# Bước 2 đã được thực hiện khi cài tunnel đầu tiên, bỏ qua
info "Bước 2: Bỏ qua cài đặt cloudflared (đã được cài đặt trước đó)."

info "Bước 3: Xóa tunnel cũ (nếu có) trên Cloudflare và tạo tunnel mới"
sudo -u "$USER_NAME" "$CLOUD_FLARED_BIN" tunnel delete -f "$TUNNEL_NAME" >/dev/null 2>&1 || true
info "Đã xóa tunnel '$TUNNEL_NAME' cũ trên Cloudflare (nếu có)."

CREATE_OUTPUT=$(sudo -u "$USER_NAME" "$CLOUD_FLARED_BIN" tunnel create "$TUNNEL_NAME")
echo "$CREATE_OUTPUT"

# Extract ID - Support both Linux (grep -P) and Mac (sed/awk)
# Clean output first to handle potential multi-line issues
CLEAN_OUTPUT=$(echo "$CREATE_OUTPUT" | tr -d '\r')

# Try standard Linux/GNU grep
TUNNEL_ID=$(echo "$CLEAN_OUTPUT" | grep -o "[a-f0-9]\{8\}-[a-f0-9]\{4\}-[a-f0-9]\{4\}-[a-f0-9]\{4\}-[a-f0-9]\{12\}" | head -n 1 || true)

if [ -z "$TUNNEL_ID" ]; then
    # Fallback/Try alternate extraction for Mac
     TUNNEL_ID=$(echo "$CLEAN_OUTPUT" | sed -n 's/.*id \([0-9a-f-]*\).*/\1/p' | head -n 1)
fi

# Extra fallback if simple grep works better on output format
if [ -z "$TUNNEL_ID" ]; then
    TUNNEL_ID=$(echo "$CLEAN_OUTPUT" | grep -oE '[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}' | head -n 1 || true)
fi

# FINAL SANITIZATION: Verify ID contains only valid characters and NO newlines
TUNNEL_ID=$(echo "$TUNNEL_ID" | tr -d '\n' | tr -d '\r' | tr -d ' ')

info "Đã tạo tunnel '$TUNNEL_NAME' mới."


if [[ ! "$TUNNEL_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    error "Không thể lấy được hoặc Tunnel ID không hợp lệ: '$TUNNEL_ID'. Vui lòng kiểm tra lại output của lệnh create."
fi
info "Tunnel ID: $TUNNEL_ID"

CREDENTIALS_FILE="$CONFIG_DIR/$TUNNEL_ID.json"
CONFIG_FILE="$CONFIG_DIR/config-$TUNNEL_NAME.yaml"

info "Bước 4: Tạo DNS Record cho $DOMAIN trỏ đến tunnel"
sudo -u "$USER_NAME" "$CLOUD_FLARED_BIN" tunnel route dns "$TUNNEL_NAME" "$DOMAIN"

info "Bước 5: Tạo file cấu hình $CONFIG_FILE"
sudo -u "$USER_NAME" bash -c "cat > '$CONFIG_FILE'" <<EOF
# File cấu hình được tạo tự động cho tunnel $TUNNEL_NAME
# Lưu ý: tunnel ID và credentials-file khác với tunnel n8n
tunnel: $TUNNEL_ID
credentials-file: $CREDENTIALS_FILE

ingress:
  - hostname: $DOMAIN
    service: http://localhost:$LOCAL_PORT
  - service: http_status:404
EOF
info "Đã tạo file cấu hình thành công."

info "Bước 6: Tạo và cấu hình service"

if [[ "$OS_TYPE" == "Darwin" ]]; then
    # macOS Service (LaunchDaemon)
    SERVICE_NAME="com.cloudflare.cloudflared.$TUNNEL_NAME"
    PLIST_PATH="/Library/LaunchDaemons/$SERVICE_NAME.plist"

    sudo bash -c "cat > '$PLIST_PATH'" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$SERVICE_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$CLOUD_FLARED_BIN</string>
        <string>--config</string>
        <string>$CONFIG_FILE</string>
        <string>tunnel</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>UserName</key>
    <string>$USER_NAME</string>
    <key>StandardOutPath</key>
    <string>/tmp/$SERVICE_NAME.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/$SERVICE_NAME.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF
    info "Đã tạo file plist tại $PLIST_PATH"

    info "Bước 7: Kích hoạt và khởi động dịch vụ"
    # Load the service
    # Fix permissions
    sudo chown root:wheel "$PLIST_PATH"
    sudo chmod 644 "$PLIST_PATH"
    
    sudo launchctl bootstrap system "$PLIST_PATH"

    info "--- HOÀN THÀNH ---"
    echo "Tunnel '$TUNNEL_NAME' đã được cài đặt và khởi chạy."
    echo "Kiểm tra trạng thái bằng lệnh:"
    echo -e "${YELLOW}sudo launchctl list | grep $SERVICE_NAME${NC}"
    echo "Kiểm tra log tại /tmp/$SERVICE_NAME.err.log"

else
    # Linux Service (Systemd)
    SERVICE_FILE="/etc/systemd/system/cloudflared-$TUNNEL_NAME.service"
    sudo bash -c "cat > '$SERVICE_FILE'" <<EOF
[Unit]
Description=Cloudflare Tunnel for $TUNNEL_NAME ($DOMAIN)
After=network.target

[Service]
Type=simple
# Mỗi tunnel sẽ có một file config riêng
ExecStart=$CLOUD_FLARED_BIN --config $CONFIG_FILE tunnel run
Restart=on-failure
RestartSec=5
User=$USER_NAME
Group=$(id -gn "$USER_NAME")
Environment=HOME=$HOME_DIR
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cloudflared-$TUNNEL_NAME

[Install]
WantedBy=multi-user.target
EOF
    info "Đã tạo file service tại $SERVICE_FILE"

    info "Bước 7: Kích hoạt và khởi động dịch vụ"
    sudo systemctl daemon-reload
    sudo systemctl enable "cloudflared-$TUNNEL_NAME.service"
    sudo systemctl start "cloudflared-$TUNNEL_NAME.service"

    info "--- HOÀN THÀNH ---"
    echo "Tunnel '$TUNNEL_NAME' đã được cài đặt và khởi chạy."
    echo "Kiểm tra trạng thái dịch vụ:"
    echo -e "${YELLOW}  sudo systemctl status cloudflared-$TUNNEL_NAME.service${NC}"
    echo "Xem log realtime:"
    echo -e "${YELLOW}  sudo journalctl -u cloudflared-$TUNNEL_NAME.service -f${NC}"
fi