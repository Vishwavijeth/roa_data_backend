import smtplib
import ssl
from email.message import EmailMessage

from config import (
    POSTMARK_USER_ACCESS_KEY,
    POSTMARK_USER_SECRET_KEY,
    EMAIL_SENDER_NAME,
    EMAIL_SENDER_ADDRESS,
    POSTMARK_SMTP_HOST,
    POSTMARK_SMTP_PORT,
)


def send_mail(
    subject: str,
    body: str,
    to_email: str,
) -> bool:
    mail = EmailMessage()

    mail["Subject"] = subject
    mail["From"] = f"{EMAIL_SENDER_NAME} <{EMAIL_SENDER_ADDRESS}>"
    mail["To"] = to_email

    mail.set_content(body)

    try:
        print("=" * 60)
        print("EMAIL DEBUG")
        print("=" * 60)

        print("SMTP Host:", POSTMARK_SMTP_HOST)
        print("SMTP Port:", POSTMARK_SMTP_PORT)
        print("From:", EMAIL_SENDER_ADDRESS)
        print("To:", to_email)

        tls_context = ssl.create_default_context()

        print("\n1. Connecting to Postmark SMTP...")

        with smtplib.SMTP(
            POSTMARK_SMTP_HOST,
            POSTMARK_SMTP_PORT,
            timeout=10,
        ) as smtp:

            # =================================================
            # ENABLE SMTP DEBUGGING
            # =================================================

            smtp.set_debuglevel(1)

            # =================================================
            # EHLO
            # =================================================

            print("\n2. Sending EHLO...")

            code, response = smtp.ehlo()

            print("EHLO CODE:", code)
            print(
                "EHLO RESPONSE:",
                response.decode(
                    errors="replace"
                ),
            )

            # =================================================
            # START TLS
            # =================================================

            print("\n3. Starting TLS...")

            code, response = smtp.starttls(
                context=tls_context
            )

            print("STARTTLS CODE:", code)
            print(
                "STARTTLS RESPONSE:",
                response.decode(
                    errors="replace"
                ),
            )

            # EHLO again after TLS
            smtp.ehlo()

            # =================================================
            # LOGIN
            # =================================================

            print("\n4. Authenticating with Postmark...")

            code, response = smtp.login(
                POSTMARK_USER_ACCESS_KEY,
                POSTMARK_USER_SECRET_KEY,
            )

            print("LOGIN CODE:", code)
            print(
                "LOGIN RESPONSE:",
                response.decode(
                    errors="replace"
                ),
            )

            # =================================================
            # SEND MAIL
            #
            # Doing MAIL / RCPT / DATA manually lets us inspect
            # Postmark's response after receiving the message.
            # =================================================

            print("\n5. Sending MAIL FROM...")

            code, response = smtp.mail(
                EMAIL_SENDER_ADDRESS
            )

            print("MAIL FROM CODE:", code)
            print(
                "MAIL FROM RESPONSE:",
                response.decode(
                    errors="replace"
                ),
            )

            if code not in (250, 251):
                print(
                    "\nFAILED: Postmark rejected sender"
                )
                return False

            # =================================================
            # RECIPIENT
            # =================================================

            print("\n6. Sending RCPT TO...")

            code, response = smtp.rcpt(
                to_email
            )

            print("RCPT TO CODE:", code)
            print(
                "RCPT TO RESPONSE:",
                response.decode(
                    errors="replace"
                ),
            )

            if code not in (250, 251):
                print(
                    "\nFAILED: Postmark rejected recipient"
                )
                return False

            # =================================================
            # SEND MESSAGE DATA
            # =================================================

            print("\n7. Sending email content...")

            code, response = smtp.data(
                mail.as_bytes()
            )

            response_text = response.decode(
                errors="replace"
            )

            print("\nDATA CODE:", code)
            print(
                "DATA RESPONSE:",
                response_text,
            )

            # =================================================
            # CHECK FINAL POSTMARK RESPONSE
            # =================================================

            if code == 250:
                print("\n" + "=" * 60)

                print(
                    "SUCCESS: EMAIL ACCEPTED BY POSTMARK"
                )

                print(
                    "Recipient:",
                    to_email,
                )

                print(
                    "Postmark response:",
                    response_text,
                )

                print(
                    "\nIMPORTANT: This confirms Postmark "
                    "accepted the email."
                )

                print(
                    "It does NOT confirm that Gmail has "
                    "delivered it to the inbox."
                )

                print("=" * 60)

                return True

            print("\n" + "=" * 60)

            print(
                "FAILED: POSTMARK DID NOT ACCEPT EMAIL"
            )

            print(
                "SMTP code:",
                code,
            )

            print(
                "SMTP response:",
                response_text,
            )

            print("=" * 60)

            return False

    except smtplib.SMTPAuthenticationError as exc:
        print("\nEMAIL ERROR TYPE: SMTPAuthenticationError")
        print("SMTP CODE:", exc.smtp_code)
        print("SMTP ERROR:", exc.smtp_error)

        return False

    except smtplib.SMTPSenderRefused as exc:
        print("\nEMAIL ERROR TYPE: SMTPSenderRefused")
        print("SMTP CODE:", exc.smtp_code)
        print("SMTP ERROR:", exc.smtp_error)
        print("SENDER:", exc.sender)

        return False

    except smtplib.SMTPRecipientsRefused as exc:
        print("\nEMAIL ERROR TYPE: SMTPRecipientsRefused")
        print("RECIPIENTS:", exc.recipients)

        return False

    except smtplib.SMTPResponseException as exc:
        print("\nEMAIL ERROR TYPE: SMTPResponseException")
        print("SMTP CODE:", exc.smtp_code)
        print("SMTP ERROR:", exc.smtp_error)

        return False

    except smtplib.SMTPException as exc:
        print("\nEMAIL ERROR TYPE: SMTPException")
        print("EMAIL ERROR:", str(exc))

        return False

    except Exception as exc:
        print(
            "\nEMAIL ERROR TYPE:",
            type(exc).__name__,
        )

        print(
            "EMAIL ERROR:",
            str(exc),
        )

        return False