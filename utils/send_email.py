from email.mime.text import MIMEText
from smtplib import SMTP_SSL
import yaml
import hydra
from omegaconf import DictConfig, OmegaConf
from rich import print as rprint
from rich.syntax import Syntax

@hydra.main(version_base=None, config_path="../config", config_name="config.yaml")
def main(cfg: DictConfig):
    # 支持命令行参数覆盖
    rprint('[bold magenta]=== Config ===[/bold magenta]')
    rprint(Syntax(OmegaConf.to_yaml(cfg), 'yaml', line_numbers=True, theme='monokai'))
    subject = cfg.get('subject', 'PythonEmailTest')
    content = cfg.get('content', 'Python email test...')
    to_email = cfg.email.get('to_email', '')
    # 允许通过命令行覆盖邮件参数
    # send_email(cfg, subject, content, to_email)

# 保持 send_email 兼容 DictConfig
def send_email(cfg, subject, content, to_email):
    smtp_server = cfg.email.smtp_server
    smtp_port = cfg.email.smtp_port
    smtp_user = cfg.email.smtp_user
    smtp_password = cfg.email.smtp_password
    msg = MIMEText(content, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email
    smtp = SMTP_SSL(smtp_server, smtp_port)
    smtp.login(smtp_user, smtp_password)
    smtp.sendmail(smtp_user, to_email.split(','), msg.as_string())
    smtp.quit()

if __name__ == "__main__":
    main()