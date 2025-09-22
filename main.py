import os
import json
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

class ZohoDeskAPI:
    def __init__(self):
        # Hard-coded credentials
        self.client_id = "1000.YTGWJ9XNXKTX7QQCW3CJYG7KLF6KHG"
        self.client_secret = "7df24f3a04d94a67a1b8a53ebb53afcd913b927ba7"
        self.refresh_token = "1000.9587a87547cda70ae7e90764e5418919.2b57a6000a4faf52a8e2baace256a44b"
        self.org_id = "884904605"
        self.department_id = "1121831000000006907"

        self.access_token = None
        self.token_expires_at = None

    def get_access_token(self):
        """Get new access token using refresh token"""
        url = "https://accounts.zoho.com/oauth/v2/token"

        data = {
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token'
        }

        try:
            response = requests.post(url, data=data, timeout=30)
            result = response.json()

            if 'access_token' in result:
                self.access_token = result['access_token']
                self.token_expires_at = datetime.now() + timedelta(hours=1)
                logging.info("Successfully refreshed Zoho access token")
                return self.access_token
            else:
                logging.error(f"Failed to get access token: {result}")
                raise Exception(f"Failed to get access token: {result}")
        except Exception as e:
            logging.error(f"Error refreshing token: {str(e)}")
            raise

    def ensure_valid_token(self):
        """Ensure we have a valid access token"""
        if not self.access_token or datetime.now() >= self.token_expires_at:
            self.get_access_token()

    def create_ticket_from_sms(self, phone_number, message_body, sender_name=None):
        """Create a ticket in Zoho Desk from SMS data"""
        self.ensure_valid_token()

        url = "https://desk.zoho.com/api/v1/tickets"

        headers = {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'orgId': self.org_id,
            'Content-Type': 'application/json'
        }

        # Create subject and description
        subject = f"SMS from {phone_number}"
        if sender_name:
            subject = f"SMS from {sender_name} ({phone_number})"

        description = f"SMS received from {phone_number}:\n\n{message_body}\n\nTimestamp: {datetime.now().isoformat()}"

        # Create contact name from phone number if no name provided
        contact_name = sender_name if sender_name else f"SMS Customer {phone_number}"

        ticket_data = {
            'subject': subject,
            'description': description,
            'departmentId': self.department_id,
            'contact': {
                'lastName': contact_name,
                'phone': phone_number,
                'email': f"{phone_number.replace('+', '').replace(' ', '')}@sms.customer"
            },
            'priority': 'Medium',
            'status': 'Open',
            'channel': 'Phone'
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(ticket_data), timeout=30)
            result = response.json()

            if response.status_code == 200 and 'id' in result:
                logging.info(f"Successfully created ticket {result['ticketNumber']} (ID: {result['id']})")
                return {
                    'success': True,
                    'ticket_id': result['id'],
                    'ticket_number': result['ticketNumber'],
                    'contact_id': result['contactId']
                }
            else:
                logging.error(f"Failed to create ticket: {result}")
                return {
                    'success': False,
                    'error': result
                }
        except Exception as e:
            logging.error(f"Error creating ticket: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

# Lazy initialization - create API connection when needed
zoho_api = None

def get_zoho_api():
    global zoho_api
    if zoho_api is None:
        try:
            zoho_api = ZohoDeskAPI()
        except Exception as e:
            logging.error(f"Failed to initialize Zoho API: {str(e)}")
            return None
    return zoho_api

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    """Handle incoming SMS webhook (Twilio format)"""
    api = get_zoho_api()
    if not api:
        return jsonify({'error': 'Zoho API not initialized'}), 500

    try:
        # Get SMS data from webhook (Twilio format)
        sms_data = request.form if request.form else request.get_json()

        phone_number = sms_data.get('From', 'Unknown')
        message_body = sms_data.get('Body', '')
        sender_name = sms_data.get('ProfileName')

        logging.info(f"Received SMS from {phone_number}: {message_body[:50]}...")

        # Return quick response to Twilio to avoid timeout
        # Then process ticket creation asynchronously

        # Create ticket in Zoho Desk with shorter timeout
        result = api.create_ticket_from_sms(phone_number, message_body, sender_name)

        if result['success']:
            logging.info(f"Ticket created successfully: #{result['ticket_number']}")
            return jsonify({
                'success': True,
                'ticket_id': result['ticket_id'],
                'ticket_number': result['ticket_number']
            })
        else:
            logging.error(f"Failed to create ticket: {result.get('error', 'Unknown error')}")
            return jsonify({
                'success': False,
                'error': 'Failed to create support ticket'
            }), 500

    except Exception as e:
        logging.error(f"Error processing SMS webhook: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/test', methods=['POST'])
def test_endpoint():
    """Test endpoint"""
    api = get_zoho_api()
    if not api:
        return jsonify({'error': 'Zoho API not initialized'}), 500

    try:
        test_data = request.get_json() or {}
        phone = test_data.get('phone', '+1234567890')
        message = test_data.get('message', 'Test message from webhook service')
        name = test_data.get('name', 'Test User')

        result = api.create_ticket_from_sms(phone, message, name)
        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)