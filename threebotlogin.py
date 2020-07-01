import json
import random
import string
import urllib.parse
import pprint
import requests
import nacl.public
import nacl.signing
import base64
import urllib.parse
from flask import Flask, request, redirect, make_response
from nacl.public import PrivateKey, Box
from onlyoffice import OnlyOfficeAuthenticator
from config import config

"""
# Generate an x25519 key
openssl genpkey -algorithm x25519 -out private.key

# Extract raw private key, encode it in base64
openssl pkey -in private.key -text | xargs | sed -e 's/.*priv\:\(.*\)pub\:.*/\1/' | xxd -r -p | base64

# Extract raw public key, encode it in base64 (this is not needed on this code)
# Public key is generated from private key automatically
openssl pkey -in private.key -text_pub | grep '^ ' | xargs | xxd -r -p | base64
"""

class ThreeBotAuthenticator:
    def __init__(self, app, appid, privatekey):
        self.app = app
        self.appid = appid

        # Private key used to uncrypt ciphertext
        self.privkey = nacl.public.PrivateKey(privatekey, nacl.encoding.Base64Encoder)

        # Generate public key from the private key
        self.pubkey = self.privkey.public_key.encode(nacl.encoding.Base64Encoder).decode('utf-8')

        self.routes()
        self.OnlyOfficeAuthenticator = OnlyOfficeAuthenticator()


    def routes(self):
        @self.app.route('/auth/callback_threebot')
        def callback():
            if request.args.get("error"):
                message = urllib.parse.quote(request.args.get("error"))
                return "Authentication failed: %s" % message, 400

            if not request.args.get('signedAttempt'):
                return "Could not parse server response" % message, 400

            payload = json.loads(request.args.get('signedAttempt'))
            username = payload['doubleName']

            # Signedhash contains state signed by user's bot key
            signedhash = payload['signedAttempt']

            # Fetching user's bot information (including public key)
            userinfo = requests.get(f"{config['loginUrl']}/api/users/%s" % username).json()
            userpk = userinfo['publicKey']

            # Verifying state signature
            try:
                vkey = nacl.signing.VerifyKey(userpk, nacl.encoding.Base64Encoder)
                data = vkey.verify(base64.b64decode(signedhash))
                data = json.loads(data.decode('utf-8'))

            except:
                print("Invalid signed hash")
                return 'Unable to verify state signature, denied.', 400

            ukey = vkey.to_curve25519_public_key()

            # Decrypt the ciphertext with our private key and bot's public key
            try:
                box = nacl.public.Box(self.privkey, ukey)
                ciphertext = base64.b64decode(data['data']['ciphertext'])
                nonce = base64.b64decode(data['data']['nonce'])

                response = box.decrypt(ciphertext, nonce)

            except:
                print("Could not decrypt cipher")
                return 'Unable to decrypt payload, denied.', 400

            values = json.loads(response.decode('utf-8'))

            seiVerified = requests.post(f"{config['kycUrl']}/verification/verify-sei", json={ "signedEmailIdentifier": values['email']['sei'] })
            if seiVerified.status_code != 200:
                return 'Email unverified, access denied.', 400
            

            print("[+] threebot: user '%s' authenticated" % username)
            email = values["email"]['email']
            password = values['derivedSeed']
            
            self.OnlyOfficeAuthenticator.registerUser(username,email,password)
            authkey = self.OnlyOfficeAuthenticator.getAuthenticationToken(email,password)
            
            resp = make_response(redirect('http://localhost/'))
            resp.set_cookie('asc_auth_key', authkey)
            return resp

        @self.app.route('/auth/login')
        def login():
            # Public backend authenticator service
            authurl = config['loginUrl']

            # Application id, this host will be used for callback url
            callback = "/callback_threebot"

            # State is a random string
            allowed = string.ascii_letters + string.digits
            state = ''.join(random.SystemRandom().choice(allowed) for _ in range(32))

            # Encode payload with urlencode then passing data to the GET request
            payload = {
                'appid': self.appid,
                'publickey': self.pubkey,
                'state': state,
                'scope': json.dumps({'email': True,'derivedSeed': True}),
                'redirecturl': callback,
            }

            result = urllib.parse.urlencode(payload, quote_via=urllib.parse.quote)

            return redirect("%s/?%s" % (authurl, result), code=302)

def configure(app, appid, privatekey):
    app.config['threebot_config'] = dict(
        appid=appid,
        privatekey=privatekey
    )

    auth = ThreeBotAuthenticator(app, appid, privatekey)
