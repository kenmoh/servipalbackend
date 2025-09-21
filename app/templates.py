

def send_email_verification_code(code: int, expires_in: str):
    return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>Email Verification</title>
        </head>
        <body>
            <h2>Verify Your Email</h2>
            <p>Your email verification code is: <strong>{ code }</strong></p>
            <p>This code will expire in { expires_in }.</p>
        </body>
        </html>
    """

def send_password_request_email(user: str, custom_url: str, reset_url: str, expires_in: str):
   
   return f"""
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Document</title>
  </head>
  <body>
    <h1>Password Reset Request</h1>
    <p>Hello { user },</p>
    <p>
      We received a request to reset your password. If you didn't make this
      request, you can ignore this email.
    </p>
    <p>To reset your password, click the link below:</p>
    <p>
      <a href="{ custom_url }">
        Reset Password in App
      </a>
    </p>
    <p>
      <small>If the app doesn't open automatically, try this direct link: <a href="{ reset_url }">Open ServiPal App</a></small>
    </p>
    <p>
      <small>Or copy this token and paste it in the app: <strong>{ reset_url }</strong></small>
    </p>
    <p>This link will expire in {expires_in}.</p>
    <p>ServiPal Team</p>
  </body>
</html>
"""
    
def send_welcome_email_template(title: str, name: str, body: str):
    return f"""
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Welcome Email</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,200;0,400;1,100;1,300;1,700&display=swap"
      rel="stylesheet"
    />

    <style></style>
  </head>
  <body>
    <div style="padding: 7px">
      <div style="margin: 0 auto; padding: 5px">
        <h4
          style="
            color: #1b263b;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1.5;
            font-size: 18px;
            font-family: Montserrat;
          "
        >
          { title }!
        </h4>
        <h5
          style="
            color: #1b263b;
            font-size: 14px;
            line-height: 1.5;

            font-family: Montserrat;
            font-weight: bold;
          "
        >
          Hi, { name }
        </h5>
        {body}
        <br />
        <br />
        <br />
        <br />
        <br />
        <small>Best Regards</small>
        <br />
        <br />
        <strong> The SerViPal Team </strong>
      </div>
    </div>
  </body>
</html>

"""