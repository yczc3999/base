"""
SMTP 邮件通知驱动 — 纯标准库

config:
    {
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_user": "noreply@example.com",
        "smtp_pass": "xxx",
        "from_name": "Base Platform",
        "from_addr": "noreply@example.com"
    }
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.services.base import BaseDriver


class EmailDriver(BaseDriver):
    async def send(self, title: str, content: str, **kwargs) -> bool:
        host = self._get("smtp_host", required=True)
        port = int(self._get("smtp_port") or 465)
        user = self._get("smtp_user", required=True)
        password = self._get("smtp_pass", required=True)
        from_name = self._get("from_name") or "Base Platform"
        from_addr = self._get("from_addr") or user
        to_addr = kwargs.get("to", from_addr)

        if self.service.failed:
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = f"{from_name} <{from_addr}>"
        msg["To"] = to_addr

        html = f"<h2>{title}</h2><div>{content}</div>"
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=10)
            else:
                server = smtplib.SMTP(host, port, timeout=10)
                server.starttls()

            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
            server.quit()
            return True
        except Exception as e:
            self.service._fail(f"邮件发送失败: {e}")
            return False
