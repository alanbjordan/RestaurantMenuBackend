# =============================================================
#                        IMPORTS
# =============================================================

# Flask and related packages
from flask import Flask, jsonify, request, session, redirect, send_from_directory, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Standard Python libraries
import os
import logging
import json
import uuid
from io import BytesIO

# Third-party packages
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import openai
from openai import OpenAI
import qrcode
from flask_caching import Cache

# =============================================================
#                      END OF IMPORTS
# =============================================================


# =============================================================
#                  INITIAL SETUP AND CONFIGURATION
# =============================================================

# Load environment variables from .env
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Flask application setup
app = Flask(__name__, static_folder='build', static_url_path='/')
app.secret_key = os.getenv('APP_SECRET_KEY')
app_url = os.getenv('APP_URL')

# Cache configuration (using simple cache; use 'redis' for Redis-backed cache)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# CORS configuration: Allow requests from specific domains
CORS(app, resources={r"/*": {"origins": [
    app_url
]}})

# SocketIO setup with CORS allowed origins
socketio = SocketIO(app, cors_allowed_origins=[
    app_url
])

# =============================================================
#               END OF INITIAL SETUP AND CONFIGURATION
# =============================================================


# =============================================================
#              SET URLS, TOKENS, AND API KEYS
# =============================================================

# Loyverse API
API_URL = 'https://api.loyverse.com/v1.0'
MODIFIER_URL = 'https://api.loyverse.com/v1.0/modifiers'
API_TOKEN = os.getenv('API_TOKEN')

# Database
DATABASE_URL = os.getenv('DATABASE_URL')

# Skycore Wallet API
WALLET_API_URL = 'https://api.skycore.com/API/http/v3/'
WALLET_API_KEY = os.getenv
PASS_TEMPLATE_ID = os.getenv('PASS_TEMPOLATE_ID')

# OpenAI API
API_KEY = os.getenv('OPENAI_API_KEY')

if not API_KEY:
    logging.error("OpenAI API key not set")
else:
    openai.api_key = API_KEY
client = OpenAI()

# LINE Messaging API
LINE_CHANNEL_ACCESS_TOKEN =os.getenv('LINE_ACCESS')
LINE_REPLY_API_URL = 'https://api.line.me/v2/bot/message/reply'
LINE_CHANNEL_ID = os.getenv('LINE_CHANNEL_ID')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_REDIRECT_URI = os.getenv('LINE_REDIRECT_URI')
LINE_AUTH_URL = 'https://access.line.me/oauth2/v2.1/authorize'
LINE_TOKEN_URL = 'https://api.line.me/oauth2/v2.1/token'
LINE_PROFILE_URL = 'https://api.line.me/v2/profile'

# =============================================================
#          END OF URLS, TOKENS, AND API KEYS SECTION
# =============================================================


# =============================================================
#            DATABASE CONFIGURATION AND CONNECTIONS
# =============================================================

# Establishing database connection
def get_db_connection():
    logging.debug("Establishing database connection")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# =============================================================
#                 END OF DATABASE CONFIGURATION
# =============================================================


# =============================================================
#          REQUEST HANDLING AND MIDDLEWARE SETUP
# =============================================================

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*') 
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# =============================================================
#         END OF REQUEST HANDLING AND MIDDLEWARE SETUP
# =============================================================



# =============================================================
#                       ROUTE HANDLERS
# =============================================================

# -------------------------------------------------------------
#                   ITEMS ENDPOINTS
# -------------------------------------------------------------

@app.route('/api/items', methods=['GET'])
@cache.cached(timeout=86400)  # Cache for 24 hours (86400 seconds)
def get_items():
    headers = {
        'Authorization': f'Bearer {API_TOKEN}'
    }
    all_items = []
    limit = 250
    cursor = None

    while len(all_items) < 600:
        params = {
            'limit': limit,
        }
        if cursor:
            params['cursor'] = cursor

        response = requests.get(f'{API_URL}/items', headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            all_items.extend(items)
            
            # Check if there is a cursor for the next page
            cursor = data.get('cursor')
            
            # Break the loop if there are no more items to fetch
            if not cursor or len(items) < limit:
                break
        else:
            return jsonify({'error': 'Failed to fetch data'}), response.status_code

    return jsonify({'items': all_items})


# -------------------------------------------------------------
#                   MODIFIER ENDPOINTS
# -------------------------------------------------------------

@app.route('/api/modifiers', methods=['GET'])
def get_modifier():
    modifier_id = request.args.get('modifier_id')
    logging.debug(f"get_modifier called with modifier_id: {modifier_id}")
    
    if not modifier_id:
        return jsonify({'error': 'modifier_id is required'}), 400
    
    headers = {
        'Authorization': f'Bearer {API_TOKEN}'
    }

    try:
        response = requests.get(f"{MODIFIER_URL}/{modifier_id}", headers=headers)
        if response.status_code == 200:
            modifier_data = response.json()
            logging.debug(f"Modifier {modifier_id} data: {modifier_data}")
            return jsonify(modifier_data)
        else:
            logging.error(f"Failed to fetch modifier data for {modifier_id}. Status code: {response.status_code}")
            logging.error(f"Response: {response.text}")
            return jsonify({'error': 'Failed to fetch modifier data'}), 404
    except Exception as e:
        logging.error(f"Exception occurred while fetching modifier data: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# -------------------------------------------------------------
#                   ORDERS ENDPOINTS
# -------------------------------------------------------------

@app.route('/api/orders', methods=['POST'])
def create_order():
    order_data = request.json
    logging.debug(f"Received order data: {order_data}")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        insert_order_query = """
        INSERT INTO orders (table_name, order_status, comment)
        VALUES (%s, %s, %s)
        RETURNING id;
        """
        cursor.execute(insert_order_query, (order_data['table_name'], 'received', order_data.get('comment', 'No comment')))
        order_id = cursor.fetchone()['id']
        logging.debug(f"Created order with ID: {order_id}")
        
        line_items = []
        for item in order_data['line_items']:
            insert_item_query = """
            INSERT INTO order_items (order_id, item_name, quantity, selected_variant, selected_modifiers)
            VALUES (%s, %s, %s, %s, %s);
            """
            cursor.execute(insert_item_query, (
                order_id,
                item['item_name'],
                item['quantity'],
                json.dumps(item.get('selectedVariant')),
                json.dumps(item.get('selectedModifiers', []))
            ))
            logging.debug(f"Inserted item for order {order_id}: {item}")
            line_items.append({
                'item_name': item['item_name'],
                'quantity': item['quantity'],
                'selected_variant': item.get('selectedVariant'),
                'selected_modifiers': item.get('selectedModifiers', [])
            })
        
        conn.commit()
        socketio.emit('new_order', {
            'order_id': order_id,
            'table_name': order_data['table_name'],
            'comment': order_data.get('comment', 'No comment'),
            'items': order_data['line_items']
        })

        return jsonify({'message': 'Order created successfully', 'order_id': order_id}), 201
    except Exception as e:
        logging.error(f"Error creating order: {e}")
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/orders', methods=['GET'])
def get_orders():
    logging.debug("Fetching orders")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
        SELECT o.id as order_id, o.table_name, o.order_status, o.comment, 
               oi.item_name, oi.quantity, oi.selected_variant, oi.selected_modifiers
        FROM orders o
        JOIN order_items oi ON o.id = oi.order_id
        WHERE o.order_status = 'received';
        """)
        orders = cursor.fetchall()
        logging.debug(f"Fetched orders: {orders}")
        return jsonify(orders)
    except Exception as e:
        logging.error(f"Error fetching orders: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
def update_order_status(order_id):
    new_status = request.json.get('status')
    logging.debug(f"Updating order {order_id} status to {new_status}")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        update_query = """
        UPDATE orders SET order_status = %s WHERE id = %s;
        """
        cursor.execute(update_query, (new_status, order_id))
        conn.commit()
        socketio.emit('order_status_update', {'order_id': order_id, 'new_status': new_status})
        
        return jsonify({'message': 'Order status updated successfully'}), 200
    except Exception as e:
        logging.error(f"Error updating order status: {e}")
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# -------------------------------------------------------------
#                   WEBHOOKS ENDPOINTS
# -------------------------------------------------------------

@app.route('/webhook/receipts_points', methods=['POST'])
def webhook_receipts():
    payload = request.json
    logging.debug(f"Received payload: {payload}")

    try:
        receipts = payload.get('receipts', [])
        for receipt in receipts:
            loyverse_id = receipt.get('customer_id') 
            points_balance = receipt.get('points_balance', 0)

            if loyverse_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    # Update the points balance for the customer in loyalty_accounts
                    cursor.execute("UPDATE loyalty_accounts SET rewards_points = %s WHERE loyverse_id = %s", (points_balance, loyverse_id))
                    conn.commit()
                    logging.debug(f"Updated points for customer with loyverse_id {loyverse_id}: {points_balance}")
                except Exception as e:
                    logging.error(f"Error updating points: {e}")
                    conn.rollback()
                finally:
                    cursor.close()
                    conn.close()
            else:
                logging.warning(f"Loyverse ID missing for receipt: {receipt}")

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logging.error(f"Error processing receipts: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================
#                   END OF ROUTE HANDLERS
# =============================================================

# =============================================================
#            LINE OFFICIAL CHAT BOT CONFIGURATION
# =============================================================

def initialize_thread():
    """Initialize a new chat thread with predefined settings."""
    try:
        thread = client.beta.threads.create(
            messages=[
                {
                    "role": "assistant",
                    "content": (
                        "You are an expert restaurant server. Provide short answers that properly answer questions and upsell items. "
                        "Do not include sources or citations in your responses. "
                        "Use your knowledge base to answer questions about the menu. "
                        "Your responses should never be in markdown format. "
                        "Your business hours are M-Sat 9am-5:30pm, Closed on Sundays. "
                        "29/1130 Moo 12, Nong Prue, Bang Lamung, Chonburi 20150 · Phone | +66 94 290 9196."
                    ),
                }
            ]
        )
        return thread.id
    except Exception as e:
        logging.error(f"Failed to initialize thread: {e}")
        return None

def get_thread_id(user_id):
    """Retrieve the thread ID for a given user from the database."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT thread_id FROM user_threads WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result['thread_id'] if result else None

def save_thread_id(user_id, thread_id):
    """Save or update the thread ID for a given user in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_threads (user_id, thread_id) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET thread_id = EXCLUDED.thread_id",
        (user_id, thread_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

def generate_chat_response(user_id, thread_id, text_transcript):
    """Generate a chat response based on the ongoing chat and menu using threads."""
    try:
        # Add user message to the thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=text_transcript
        )
    except Exception as e:
        logging.error(f"Failed to add message to thread: {e}")
        thread_id = initialize_thread()
        if thread_id:
            save_thread_id(user_id, thread_id)
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=text_transcript
            )

    # Create and poll the run for the given thread
    run = client.beta.threads.runs.create(
        thread_id=thread_id, 
        assistant_id= os.getenv('ASSISTANT_ID_OPENAI')
    )
    run_id = run.id
    run = client.beta.threads.runs.poll(thread_id=thread_id, run_id=run_id)

    # Retrieve the latest message from the run
    response_messages = list(client.beta.threads.messages.list(thread_id=thread_id, run_id=run_id))
    response_message = response_messages[-1]  # Get the last message

    return response_message.content[0].text.value

def send_line_message(reply_token, text):
    """Send a text message to the LINE user."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    data = {
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}]
    }
    try:
        response = requests.post(LINE_REPLY_API_URL, headers=headers, json=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message to LINE: {e}")

import re

def remove_markdown(text):
    """Remove markdown formatting from the text."""
    # Replace bold (**text**) or italic (*text*) or any combination (**text**)
    text = re.sub(r'(\*\*|\*|_)(.*?)\1', r'\2', text)
    return text

@app.route('/webhook/lineChat', methods=['POST'])
def webhook():
    """Handle incoming webhook events from LINE."""
    body = request.json
    logging.debug(f"Received LINE webhook event: {body}")
    
    events = body.get('events', [])
    for event in events:
        if event['type'] == 'message' and event['message']['type'] == 'text':
            user_message = event['message']['text']
            reply_token = event['replyToken']
            
            user_id = event['source'].get('userId')
            thread_id = get_thread_id(user_id)
            
            if not thread_id:
                thread_id = initialize_thread()
                save_thread_id(user_id, thread_id)
            
            try:
                reply = generate_chat_response(user_id, thread_id, user_message)
                
                # Remove any markdown formatting before sending the message to LINE
                cleaned_reply = remove_markdown(reply)
                
                logging.debug(f"Cleaned OpenAI response: {cleaned_reply}")
                send_line_message(reply_token, cleaned_reply)
            except Exception as e:
                logging.error(f'Error occurred while processing OpenAI API request: {e}')
                send_line_message(reply_token, "Sorry, I couldn't process your request.")

    return jsonify({'status': 'success'})


# =============================================================
#            END OF LINE OFFICIAL CHAT BOT CONFIGURATION
# =============================================================

# =============================================================
#                     CUSTOMER MANAGEMENT
# =============================================================

@app.route('/api/call-waiter', methods=['POST'])
def call_waiter():
    """Emit a 'call_waiter' event to notify waiters that a customer is calling."""
    message = request.json.get('message', 'A customer is calling a waiter!')
    table_name = request.json.get('table_name', 'Unknown table')
    socketio.emit('call_waiter', {'message': message, 'table_name': table_name})
    return jsonify({'status': 'success'}), 200

# -------------------------------------------------------------
#                    LINE LOGIN HANDLERS
# -------------------------------------------------------------

@app.route('/login')
def login():
    """Redirect the user to the LINE Login authorization URL."""
    state = os.urandom(24).hex()  # Generate a random state to protect against CSRF attacks
    session['state'] = state

    login_url = (
        f"{LINE_AUTH_URL}?"
        f"response_type=code&"
        f"client_id={LINE_CHANNEL_ID}&"
        f"redirect_uri={LINE_REDIRECT_URI}&"
        f"state={state}&"
        f"scope=profile%20openid"
    )
    return redirect(login_url)

@app.route('/callback')
def callback():
    """Handle the callback from LINE after the user authorizes the app."""
    code = request.args.get('code')
    state = request.args.get('state')

    if state != session.get('state'):
        return jsonify({'error': 'Invalid state parameter'}), 400

    # Exchange the authorization code for an access token
    token_payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': LINE_REDIRECT_URI,
        'client_id': LINE_CHANNEL_ID,
        'client_secret': LINE_CHANNEL_SECRET,
    }
    token_response = requests.post(LINE_TOKEN_URL, data=token_payload)
    token_data = token_response.json()
    access_token = token_data.get('access_token')

    # Retrieve the user's profile information
    headers = {'Authorization': f'Bearer {access_token}'}
    profile_response = requests.get(LINE_PROFILE_URL, headers=headers)
    profile_data = profile_response.json()

    line_id = profile_data['userId']
    display_name = profile_data['displayName']

    # Check if the user already exists in the database using the LINE ID
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_code FROM loyalty_accounts WHERE line_id = %s", (line_id,))
    existing_user = cursor.fetchone()

    if existing_user:
        # User exists; retrieve their existing code
        new_code = existing_user[0]
        user_status = 'existing'
    else:
        # If user doesn't exist, generate a new code and store the QR code
        new_code = generate_code()
        store_qr_code(line_id, new_code, display_name)
        user_status = 'new'

    cursor.close()
    conn.close()

    frontend_url = os.getenv('FRONTEND_URL')  

    # Redirect to the appropriate frontend URL with the user ID, display name, code, and user status
    return redirect(f'{frontend_url}?line_id={line_id}&display_name={display_name}&account={new_code}&status={user_status}')

# -------------------------------------------------------------
#                    QR CODE MANAGEMENT
# -------------------------------------------------------------

def generate_code(length=10):
    """Generate a random alphanumeric code."""
    return ''.join(os.urandom(10).hex().upper()[:length])

import qrcode
from io import BytesIO
from PIL import Image

def generate_and_save_qr_code(data):
    """Generate a QR code image for the given data."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    img_byte_arr = BytesIO()
    img.save(img_byte_arr, 'PNG')

    return img_byte_arr.getvalue()

def store_qr_code(line_id, code, display_name):
    """Store the generated QR code image in the database."""
    qr_data = generate_and_save_qr_code(code)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO loyalty_accounts (line_id, customer_code, qr_code_image, display_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (line_id) DO UPDATE SET
            customer_code = EXCLUDED.customer_code,
            qr_code_image = EXCLUDED.qr_code_image,
            display_name = EXCLUDED.display_name
            """,
            (line_id, code, psycopg2.Binary(qr_data), display_name)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Error storing QR code: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

@app.route('/api/get_qr_code/<line_id>', methods=['GET'])
def get_qr_code(line_id):
    """Retrieve the QR code image for a user based on their LINE ID."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT qr_code_image FROM loyalty_accounts WHERE line_id = %s", (line_id,))
        result = cursor.fetchone()

        if result and result[0]:
            img_byte_arr = result[0]
            return send_file(BytesIO(img_byte_arr), mimetype='image/png')
        else:
            return jsonify({'error': 'QR code not found'}), 404

    except Exception as error:
        logging.error(f"Error retrieving QR code: {error}")
        return jsonify({'error': 'Failed to retrieve QR code'}), 500

    finally:
        if conn is not None:
            cursor.close()
            conn.close()

# -------------------------------------------------------------
#                    USER PROFILE MANAGEMENT
# -------------------------------------------------------------

@app.route('/api/update_customer', methods=['POST'])
def update_customer():
    """Update the customer's information in the local database and Loyverse."""
    data = request.json
    logging.debug(f"Data received for updating customer: {data}")

    # Extract data from the incoming request
    email = data.get('email')
    home_country = data.get('home_country')
    birthdate = data.get('birthdate')
    gender = data.get('gender')
    customer_code = data.get('account')

    if not customer_code:
        logging.error("Customer code is missing in the request")
        return jsonify({'error': 'customer_code is required'}), 400

    # Fetch display_name from the database
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT display_name FROM loyalty_accounts WHERE customer_code = %s
        ''', (customer_code,))
        result = cursor.fetchone()

        if result is None:
            logging.error(f"No user found with customer_code: {customer_code}")
            return jsonify({'error': 'User not found'}), 404

        display_name = result[0]
        logging.debug(f"Fetched display_name from database: {display_name}")

    except Exception as e:
        logging.error(f"Error fetching display_name from database: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

    # Proceed with the rest of the update logic using the fetched display_name
    loyverse_data = {
        "name": display_name,
        "email": email,
        "country_code": home_country,
        "customer_code": customer_code
    }

    logging.debug(f"Payload to be sent to Loyverse: {loyverse_data}")

    try:
        loyverse_response = requests.post(
            f"{API_URL}/customers",
            json=loyverse_data,
            headers={"Authorization": f"Bearer {API_TOKEN}"}
        )

        logging.debug(f"Loyverse API response status code: {loyverse_response.status_code}")
        logging.debug(f"Loyverse API response content: {loyverse_response.text}")

        loyverse_response.raise_for_status()
        loyverse_data = loyverse_response.json()
        loyverse_id = loyverse_data.get('id')  # Capture the Loyverse ID
        logging.debug(f"Loyverse response data: {loyverse_data}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error updating customer in Loyverse: {e}")
        return jsonify({'error': f"Loyverse API error: {str(e)}"}), 500

    try:
        logging.debug("Attempting to update loyalty_accounts table")
        cursor = conn.cursor()  # Reopen the cursor for the update operation
        cursor.execute('''
            UPDATE loyalty_accounts
            SET display_name = %s,
                email = %s,
                home_country = %s,
                birthdate = %s,
                gender = %s,
                loyverse_id = %s  -- Store the Loyverse ID here
            WHERE customer_code = %s
        ''', (display_name, email, home_country, birthdate, gender, loyverse_id, customer_code))

        if cursor.rowcount == 0:
            logging.debug("No rows updated. Trying to insert new row.")
            cursor.execute('''
                INSERT INTO loyalty_accounts (display_name, email, home_country, birthdate, gender, customer_code, loyverse_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (display_name, email, home_country, birthdate, gender, customer_code, loyverse_id))
            logging.debug("Inserted new row into loyalty_accounts")

        conn.commit()
        logging.debug("Database transaction committed")
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        conn.rollback()
        logging.error(f"Error updating customer data: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()



@app.route('/api/get_user_profile/<line_id>', methods=['GET'])
def get_user_profile(line_id):
    """Retrieve the user's profile information, including loyalty points, based on their LINE ID."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT line_id, display_name, email, home_country, birthdate, gender, rewards_points
            FROM loyalty_accounts
            WHERE line_id = %s
        """, (line_id,))
        
        user_profile = cursor.fetchone()

        if user_profile:
            return jsonify(user_profile), 200
        else:
            return jsonify({'error': 'User not found'}), 404
    
    except Exception as e:
        logging.error(f"Error fetching user profile: {e}")
        return jsonify({'error': 'Failed to retrieve user profile'}), 500

    finally:
        cursor.close()
        conn.close()


# -------------------------------------------------------------
#                    WEBHOOKS HANDLERS
# -------------------------------------------------------------

@app.route('/webhook/customers', methods=['POST'])
def webhook_customers():
    """Handle webhook events for customer creation, update, and deletion."""
    payload = request.json
    logging.debug(f"Received payload: {payload}")

    if payload['type'] in ['customers.create', 'customers.update', 'customers.delete']:
        if 'customers' not in payload:
            logging.error("Key 'customers' not found in payload")
            return jsonify({'error': "Key 'customers' not found in payload"}), 400

        customer_data = payload['customers'][0]

        loyverse_id = customer_data.get('id')
        rewards_points = customer_data.get('total_points', 0)
        customer_code = customer_data.get('customer_code')
        print("HERE IS THE PAYLOAD!!!!", customer_data)

        if not customer_code:
            logging.error("Customer code not found in the payload")
            return jsonify({'error': "Customer code not found in the payload"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if payload['type'] == 'customers.delete':
                cursor.execute('''
                    UPDATE loyalty_accounts SET rewards_points = 0
                    WHERE customer_code = %s;
                ''', (customer_code,))
                logging.debug(f"Marked customer as deleted for customer_code: {customer_code}")
            else:
                cursor.execute('''
                    UPDATE loyalty_accounts
                    SET loyverse_id = %s,
                        rewards_points = %s
                    WHERE customer_code = %s;
                ''', (loyverse_id, rewards_points, customer_code))
                logging.debug(f"Updated customer data for customer_code: {customer_code}")

            conn.commit()
        except Exception as e:
            logging.error(f"Error updating customer data: {e}")
            conn.rollback()
            return jsonify({'error': str(e)}), 500
        finally:
            cursor.close()
            conn.close()

        return jsonify({'status': 'success'}), 200
    else:
        return jsonify({'error': 'Unhandled event type'}), 400

# =============================================================
#                 END OF CUSTOMER MANAGEMENT
# =============================================================

# =============================================================
#                FRONTEND ROUTING HANDLER
# =============================================================

# Catch-all route to serve the React frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    """Serve the React frontend for any route not explicitly handled by the API."""
    print(f"Caught path: {path}")
    return send_from_directory('build', 'index.html')

# =============================================================
#                OLD SERVER CHAT HANDLER
# =============================================================

def initialize_thread2():
    """Initialize a new chat thread for the old server chat functionality."""
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "assistant",
                "content": (
                    "You are an expert restaurant server named Qulture. Provide short answers that properly answer questions and upsell items. "
                    "Do not include sources or citations in your responses. "
                    "Use your knowledge base to answer questions about the menu. "
                    ">>> IMPORTANT :::: Do not use markdown language in your responses ::: <<< IMPORTANT"
                ),
            }
        ]
    )
    return thread.id

def generate_chat_response2(thread_id, text_transcript):
    """Generate a chat response for the old server chat based on the ongoing chat and menu using threads."""
    # Add user message to the thread
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=text_transcript
    )

    # Create and poll the run for the given thread
    run = client.beta.threads.runs.create(
        thread_id=thread_id, 
        assistant_id = os.getenv('ASSISTANT_ID_OPENAI')
    )
    run_id = run.id
    run = client.beta.threads.runs.poll(thread_id=thread_id, run_id=run_id)

    # Retrieve the latest message from the run
    response_messages = list(client.beta.threads.messages.list(thread_id=thread_id, run_id=run_id))
    response_message = response_messages[-1]  # Get the last message

    # Extract and return the text content from the response message
    return response_message.content[0].text.value

# =============================================================
#               END OF OLD SERVER CHAT HANDLER
# =============================================================

if __name__ == '__main__':
    logging.debug("Starting Flask application")
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
