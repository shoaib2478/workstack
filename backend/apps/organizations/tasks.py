from celery import shared_task
import structlog
import time

logger = structlog.get_logger("workstack")

@shared_task
def send_magic_link_email(email: str, magic_token: str):
    """
    Simulates sending an email via SendGrid/AWS SES.
    """
    logger.info("Starting email task...", email=email)    
    
    # Simulate network latency of an external API call
    time.sleep(3)     
    # In reality, we will use django.core.mail.send_mail here
    logger.info(
        "email_sent", 
        email=email, 
        link=f"http://localhost:3000/accept-invite?token={magic_token}"
    )
    return "Success"