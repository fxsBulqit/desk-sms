import os
import json
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import logging
from twilio.rest import Client

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

class TwilioSMS:
    def __init__(self):
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.messaging_service_sid = os.environ.get('TWILIO_MESSAGING_SERVICE_SID')

        if not all([self.account_sid, self.auth_token, self.messaging_service_sid]):
            raise Exception("Missing Twilio environment variables")

        self.client = Client(self.account_sid, self.auth_token)

    def send_sms(self, to_phone, message_body):
        """Send SMS using Twilio Messaging Service"""
        try:
            message = self.client.messages.create(
                messaging_service_sid=self.messaging_service_sid,
                body=message_body,
                to=to_phone
            )

            logging.info(f"SMS sent successfully to {to_phone}: {message.sid}")
            return {
                'success': True,
                'message_sid': message.sid,
                'to': to_phone
            }
        except Exception as e:
            logging.error(f"Failed to send SMS to {to_phone}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

class ZohoDeskAPI:
    def __init__(self):
        # Hard-coded credentials
        self.client_id = "1000.YTGWJ9XNXKTX7QQCW3CJYG7KLF6KHG"
        self.client_secret = "7df24f3a04d94a67a1b8a53ebb53afcd913b927ba7"
        self.refresh_token = "1000.40a4b2d6218f6da98702a2ff7f2cd87e.733a08fb92aa860badec806c9016a694"
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

    def get_ticket_phone_number(self, ticket_id):
        """Extract phone number from ticket contact information"""
        self.ensure_valid_token()

        url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}"

        headers = {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'orgId': self.org_id
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                ticket = response.json()

                subject = ticket.get('subject', '')
                description = ticket.get('description', '')
                logging.info(f"Ticket {ticket_id}: subject='{subject}'")

                # Try contact phone first
                if 'contact' in ticket and ticket['contact']:
                    phone = ticket['contact'].get('phone', '')
                    if phone:
                        logging.info(f"Found phone number {phone} in contact for ticket {ticket_id}")
                        return phone

                # Extract from subject (format: "SMS from +1234567890")
                if 'SMS from' in subject:
                    import re
                    phone_match = re.search(r'\+\d{10,15}', subject)
                    if phone_match:
                        phone = phone_match.group()
                        logging.info(f"Found phone number {phone} in subject for ticket {ticket_id}")
                        return phone

                # Extract from description as backup
                if description:
                    import re
                    phone_match = re.search(r'\+\d{10,15}', description)
                    if phone_match:
                        phone = phone_match.group()
                        logging.info(f"Found phone number {phone} in description for ticket {ticket_id}")
                        return phone

                logging.warning(f"No phone number found for ticket {ticket_id}")
                return None
            else:
                logging.error(f"Failed to get ticket {ticket_id}: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logging.error(f"Error getting ticket {ticket_id}: {str(e)}")
            return None

    def search_tickets_by_phone(self, phone_number):
        """Search for existing tickets by phone number"""
        self.ensure_valid_token()

        # First, let's try to get all tickets for this contact
        # We'll search using the tickets endpoint with contact phone filter
        url = "https://desk.zoho.com/api/v1/tickets"

        headers = {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'orgId': self.org_id
        }

        # Clean phone number for search
        clean_phone = phone_number.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

        params = {
            'sortBy': 'modifiedTime',
            'limit': 50,
            'include': 'contacts'
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                result = response.json()
                all_tickets = result.get('data', [])

                # Filter tickets by phone number
                matching_tickets = []
                for ticket in all_tickets:
                    if 'contact' in ticket and ticket['contact']:
                        contact_phone = ticket['contact'].get('phone', '')
                        if contact_phone:
                            # Clean the contact phone for comparison
                            clean_contact_phone = contact_phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
                            if clean_phone in clean_contact_phone or clean_contact_phone in clean_phone:
                                matching_tickets.append(ticket)

                logging.info(f"Found {len(matching_tickets)} existing tickets for {phone_number}")
                return matching_tickets
            else:
                logging.warning(f"Search failed: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            logging.error(f"Error searching tickets: {str(e)}")
            return []

    def add_comment_to_ticket(self, ticket_id, message_body, phone_number):
        """Add SMS as comment to existing ticket and reopen/prioritize it"""
        self.ensure_valid_token()

        # First, add the comment
        comment_url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}/comments"

        headers = {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'orgId': self.org_id,
            'Content-Type': 'application/json'
        }

        # Format timestamp nicely
        now = datetime.now()
        formatted_time = now.strftime("%B %d, %Y at %I:%M %p")

        comment_data = {
            'content': f"📱 **NEW SMS from {phone_number}**\n\n{message_body}\n\n*Received: {formatted_time}*",
            'contentType': 'plainText',
            'isPublic': True
        }

        try:
            comment_response = requests.post(comment_url, headers=headers, data=json.dumps(comment_data), timeout=30)
            comment_result = comment_response.json()

            if comment_response.status_code == 200 and 'id' in comment_result:
                logging.info(f"Successfully added comment to ticket {ticket_id}")

                # Now update the ticket to make it visible
                ticket_url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}"

                # First get the current ticket to preserve subject
                get_response = requests.get(ticket_url, headers=headers, timeout=30)
                if get_response.status_code == 200:
                    current_ticket = get_response.json()
                    original_subject = current_ticket.get('subject', 'Support Ticket')

                    # Update ticket to reopen and prioritize (don't change subject)
                    ticket_update = {
                        'status': 'Open',  # Reopen if closed
                        'priority': 'High'  # Escalate priority for SMS
                    }

                    update_response = requests.patch(ticket_url, headers=headers, data=json.dumps(ticket_update), timeout=30)
                else:
                    # Fallback if we can't get current ticket
                    ticket_update = {
                        'status': 'Open',
                        'priority': 'High'
                    }

                    update_response = requests.patch(ticket_url, headers=headers, data=json.dumps(ticket_update), timeout=30)

                if update_response.status_code == 200:
                    logging.info(f"Successfully reopened and prioritized ticket {ticket_id} - Status: Open, Priority: High")
                else:
                    logging.warning(f"Comment added but failed to update ticket status: {update_response.status_code} - {update_response.text}")

                return {
                    'success': True,
                    'comment_id': comment_result['id'],
                    'ticket_id': ticket_id,
                    'ticket_updated': update_response.status_code == 200
                }
            else:
                logging.error(f"Failed to add comment: {comment_result}")
                return {
                    'success': False,
                    'error': comment_result
                }
        except Exception as e:
            logging.error(f"Error adding comment: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

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

        # Format timestamp nicely for new tickets too
        now = datetime.now()
        formatted_time = now.strftime("%B %d, %Y at %I:%M %p")

        description = f"📱 **SMS received from {phone_number}**\n\n{message_body}\n\n*Received: {formatted_time}*"

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

# Lazy initialization - create API connections when needed
zoho_api = None
twilio_sms = None

def get_zoho_api():
    global zoho_api
    if zoho_api is None:
        try:
            zoho_api = ZohoDeskAPI()
        except Exception as e:
            logging.error(f"Failed to initialize Zoho API: {str(e)}")
            return None
    return zoho_api

def get_twilio_sms():
    global twilio_sms
    if twilio_sms is None:
        try:
            # Debug environment variables
            account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
            auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
            messaging_service_sid = os.environ.get('TWILIO_MESSAGING_SERVICE_SID')

            logging.info(f"Twilio env vars: SID={account_sid[:8]}..., TOKEN={'SET' if auth_token else 'MISSING'}, MSG_SID={messaging_service_sid[:8] if messaging_service_sid else 'MISSING'}")

            twilio_sms = TwilioSMS()
            logging.info("Twilio SMS initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize Twilio SMS: {str(e)}")
            return None
    return twilio_sms

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

        # Check for existing tickets from this phone number first
        existing_tickets = api.search_tickets_by_phone(phone_number)

        if existing_tickets:
            # Add SMS as comment to most recent ticket
            most_recent_ticket = existing_tickets[0]  # Already sorted by modifiedTime
            ticket_id = most_recent_ticket['id']
            ticket_number = most_recent_ticket['ticketNumber']

            logging.info(f"Adding SMS to existing ticket #{ticket_number} (ID: {ticket_id})")
            result = api.add_comment_to_ticket(ticket_id, message_body, phone_number)

            if result['success']:
                logging.info(f"SMS added as comment to ticket #{ticket_number}")
                return jsonify({
                    'success': True,
                    'action': 'comment_added',
                    'ticket_id': ticket_id,
                    'ticket_number': ticket_number,
                    'comment_id': result['comment_id']
                })
            else:
                # Fall back to creating new ticket if comment fails
                logging.warning(f"Failed to add comment, creating new ticket instead")
                result = api.create_ticket_from_sms(phone_number, message_body, sender_name)
        else:
            # No existing tickets, create new one
            logging.info(f"No existing tickets found for {phone_number}, creating new ticket")
            result = api.create_ticket_from_sms(phone_number, message_body, sender_name)

        if result['success']:
            logging.info(f"Ticket created successfully: #{result['ticket_number']}")
            return jsonify({
                'success': True,
                'action': 'new_ticket_created',
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

@app.route('/send-sms', methods=['GET', 'POST'])
def send_sms_endpoint():
    """Send SMS reply from Zoho Desk agent response"""

    # Handle GET requests (webhook validation)
    if request.method == 'GET':
        return jsonify({'status': 'Webhook endpoint ready'}), 200

    zoho_api = get_zoho_api()
    twilio_api = get_twilio_sms()

    if not zoho_api:
        return jsonify({'error': 'Zoho API not initialized'}), 500
    if not twilio_api:
        return jsonify({'error': 'Twilio SMS not initialized'}), 500

    try:
        # Get webhook data from Zoho Desk
        webhook_data = request.get_json() or {}
        logging.info(f"Received webhook data: {json.dumps(webhook_data)[:500]}...")

        # Handle Zoho webhook format (array with payload object)
        if isinstance(webhook_data, list) and len(webhook_data) > 0:
            event_data = webhook_data[0]
            payload = event_data.get('payload', {})

            ticket_id = payload.get('ticketId')
            # Strip HTML from comment content to get plain text
            import re
            comment_content = payload.get('content', '')
            comment_content = re.sub('<[^<]+?>', '', comment_content)  # Remove HTML tags
            comment_content = comment_content.strip()

            # Skip SMS-generated comments to avoid feedback loops
            if comment_content.startswith('📱') or 'NEW SMS from' in comment_content or 'SMS received from' in comment_content:
                logging.info(f"Skipping SMS-generated comment: '{comment_content[:50]}...'")
                return jsonify({'message': 'SMS comment skipped - no feedback loop'}), 200

            logging.info(f"Parsed from webhook: ticket_id={ticket_id}, content='{comment_content}'")

        else:
            # Fallback for direct API calls (testing)
            ticket_id = webhook_data.get('ticketId') or webhook_data.get('ticket_id')
            comment_content = webhook_data.get('content') or webhook_data.get('message', '')
            logging.info(f"Parsed from direct call: ticket_id={ticket_id}, content='{comment_content}'")

        if not ticket_id:
            logging.error("Missing ticket ID in webhook data")
            return jsonify({'error': 'Missing ticket ID'}), 400
        if not comment_content:
            logging.error("Missing message content in webhook data")
            return jsonify({'error': 'Missing message content'}), 400

        # Get phone number from ticket
        phone_number = zoho_api.get_ticket_phone_number(ticket_id)
        if not phone_number:
            return jsonify({'error': 'No phone number found for this ticket'}), 400

        # Send SMS
        result = twilio_api.send_sms(phone_number, comment_content)

        if result['success']:
            logging.info(f"SMS reply sent to {phone_number} for ticket {ticket_id}")
            return jsonify({
                'success': True,
                'message_sid': result['message_sid'],
                'to': phone_number,
                'ticket_id': ticket_id
            })
        else:
            logging.error(f"Failed to send SMS reply: {result['error']}")
            return jsonify({
                'success': False,
                'error': result['error']
            }), 500

    except Exception as e:
        logging.error(f"Error in send-sms endpoint: {str(e)}")
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