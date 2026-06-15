import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

def send_deals_email():
    load_dotenv()
    
    sender_email = os.getenv("EMAIL_SENDER")
    sender_password = os.getenv("EMAIL_PASSWORD")
    recipient_email = os.getenv("EMAIL_RECIPIENT")
    
    if not sender_email or not sender_password or not recipient_email:
        print("Email credentials not fully set in .env. Skipping email.")
        return False

    deals_file = Path("deals_today.html")
    if not deals_file.exists():
        print(f"File {deals_file} does not exist. Nothing to send.")
        return False

    html_content = deals_file.read_text(encoding="utf-8")

    msg = MIMEMultipart('alternative')
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg['Subject'] = f"SGProof Daily Deals Report - {now}"
    msg['From'] = sender_email
    msg['To'] = recipient_email

    part2 = MIMEText(html_content, 'html')
    msg.attach(part2)

    try:
        # Using Gmail's SMTP server as default, can be modified
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully.")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

if __name__ == "__main__":
    send_deals_email()
