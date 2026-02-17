static ref

curl -L https://github.com/novnc/noVNC/archive/refs/tags/v1.4.0.tar.gz 

# Create vendor directory
mkdir -p static/js/vendor static/css/vendor

# xterm
curl -L -o static/css/vendor/xterm.css "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css"
curl -L -o static/js/vendor/xterm.js "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"
curl -L -o static/js/vendor/xterm-addon-fit.js "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"
curl -L -o static/js/vendor/xterm-addon-web-links.js "https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.js"

# socket.io
curl -L -o static/js/vendor/socket.io.min.js "https://cdn.socket.io/4.7.2/socket.io.min.js"

