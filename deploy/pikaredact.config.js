// pm2 ecosystem for PikaRedact.  Start:  pm2 start deploy/pikaredact.config.js && pm2 save
// Served behind nginx at /PikaRedact/ (see nginx-pikaredact.conf); uvicorn listens on localhost only.
const path = require("path");
const ROOT = path.resolve(__dirname, "..");

module.exports = {
  apps: [
    {
      name: "pikaredact",
      cwd: ROOT,
      script: path.join(ROOT, "venv/bin/python"),
      args: "-m uvicorn app.main:app --host 127.0.0.1 --port 8020 --root-path /PikaRedact --proxy-headers --forwarded-allow-ips 127.0.0.1",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      max_memory_restart: "2G",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHAINLP_DATA_DIR: path.join(process.env.HOME || ROOT, "pythainlp-data"),
        PDF_THAI_FONT: "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
      },
    },
  ],
};
