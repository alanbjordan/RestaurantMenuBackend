from flask import Flask, jsonify, request, session, redirect, send_from_directory, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import requests
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import logging
import json
import openai
import uuid
from openai import OpenAI
import qrcode
from io import BytesIO
from flask import send_file



# Load environment variables from .env
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__, static_folder='build', static_url_path='/')
app.secret_key = 'b1a2c3d4e5f67890abcd1234ef567890'

# Allow requests from your Netlify domain and localhost for development
CORS(app, resources={r"/*": {"origins": ["https://qultureloungemenudevelopment.netlify.app", "http://localhost:3000"]}})
socketio = SocketIO(app, cors_allowed_origins=["https://qultureloungemenudevelopment.netlify.app", "http://localhost:3000"])

API_URL = 'https://api.loyverse.com/v1.0'
API_TOKEN = os.getenv('API_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
WALLET_API_URL = 'https://api.skycore.com/API/http/v3/'
WALLET_API_KEY = "Fh8t0vD39GQKQsy8jvV57DC4LSpHHlUT"
PASS_TEMPLATE_ID = '83e6d7afcd00479cf726762bc4b5c21e4e5cc8d1'
MODIFIER_URL = 'https://api.loyverse.com/v1.0/modifiers'
API_KEY = os.getenv('OPENAI_API_KEY')

if not API_KEY:
    logging.error("OpenAI API key not set")
else:
    openai.api_key = API_KEY

client = OpenAI()

LINE_CHANNEL_ACCESS_TOKEN = 'BnL2LlpBueGDVZWZr5lqLCLO8G9lj6OOyoKefNXCJ8XEN2uDSM4tMq5Is+1G3bGTXbqbqRCB0dn7n+ALg3CoRqto9oTamUv8/SsDu6IKZHUc4uYrQWo+0jaKIawKe1WeX6A3JE3k6LxGXin+HheVKQdB04t89/1O/w1cDnyilFU='
LINE_REPLY_API_URL = 'https://api.line.me/v2/bot/message/reply'
# new addition
LINE_CHANNEL_ID = '2006031747'
LINE_CHANNEL_SECRET = '807a6d71807843f3e8d871aaca5ccb3e'
LINE_REDIRECT_URI = 'https://qulturemenuflaskbackend-5969f5ac152a.herokuapp.com/callback'
LINE_AUTH_URL = 'https://access.line.me/oauth2/v2.1/authorize'
LINE_TOKEN_URL = 'https://api.line.me/oauth2/v2.1/token'
LINE_PROFILE_URL = 'https://api.line.me/v2/profile'
# new addition

def get_db_connection():
    logging.debug("Establishing database connection")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')  # Avoid setting this manually if Flask-CORS is used
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


@app.route('/api/items', methods=['GET'])
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

@app.route('/webhook/customers', methods=['POST'])
def webhook_customers():
    payload = request.json
    logging.debug(f"Received payload: {payload}")

    if payload['type'] in ['customers.create', 'customers.update', 'customers.delete']:
        if 'customers' not in payload:
            logging.error("Key 'customers' not found in payload")
            return jsonify({'error': "Key 'customers' not found in payload"}), 400

        customer_data = payload['customers'][0]  # Extract the first customer data from the list

        user_id = customer_data.get('id')
        account_name = customer_data.get('name')
        user_email = customer_data.get('email')
        home_country = customer_data.get('country_code')
        points = customer_data.get('total_points', 0)

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if payload['type'] == 'customers.delete':
                # Handle customer deletion by setting points to 0 or marking the record as deleted
                cursor.execute('''
                    UPDATE loyalty_signups SET points = 0
                    WHERE user_id = %s;
                ''', (user_id,))
                logging.debug(f"Marked customer as deleted for user_id: {user_id}")
            else:
                # Handle customer creation or update
                cursor.execute('''
                    INSERT INTO loyalty_signups (user_id, account_name, user_email, home_country, points)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        account_name = EXCLUDED.account_name,
                        user_email = EXCLUDED.user_email,
                        home_country = EXCLUDED.home_country,
                        points = EXCLUDED.points;
                ''', (user_id, account_name, user_email, home_country, points))
                logging.debug(f"Inserted/Updated customer data for user: {user_id}")

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

    
@app.route('/webhook/receipts_points', methods=['POST'])
def webhook_receipts():
    payload = request.json
    logging.debug(f"Received payload: {payload}")

    try:
        receipts = payload.get('receipts', [])
        for receipt in receipts:
            customer_id = receipt.get('customer_id')
            points_balance = receipt.get('points_balance', 0)

            if customer_id:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    # Fetch the card_number from the database
                    cursor.execute("SELECT card_number FROM loyalty_signups WHERE user_id = %s", (customer_id,))
                    result = cursor.fetchone()

                    if result:
                        card_number = result[0]

                        # Update the points balance for the customer
                        cursor.execute("UPDATE loyalty_signups SET points = %s WHERE user_id = %s", (points_balance, customer_id))
                        conn.commit()
                        logging.debug(f"Updated points for customer {customer_id}: {points_balance}")

                        # Update pass data at Wallet That
                        headers = {
                            "X-Api-Key": WALLET_API_KEY,
                            "Content-Type": "application/json"
                        }
                        payload = {
                            "action": "updatepassdata",
                            "pass-id": card_number,
                            "pass-template-id": PASS_TEMPLATE_ID,
                            "pass-data": {
                                "barcode-value": card_number,
                                "loyalty-points": points_balance
                            }
                        }
                        response = requests.post(WALLET_API_URL, headers=headers, json=payload)
                        if response.status_code == 200:
                            logging.debug(f"Pass updated successfully for user: {customer_id}")
                        else:
                            logging.error(f"Failed to update pass for user: {customer_id} - {response.text}")
                    else:
                        logging.warning(f"Customer ID {customer_id} not found in the database")
                except Exception as e:
                    logging.error(f"Error updating points: {e}")
                    conn.rollback()
                finally:
                    cursor.close()
                    conn.close()

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logging.error(f"Error processing receipts: {e}")
        return jsonify({'error': str(e)}), 500

def initialize_thread2():
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "assistant",
                "content": "You are an expert restaurant server. Provide short answers that properly answer questions and upsell items. "
                           "Do not include sources or citations in your responses. "
                           "Use your knowledge base to answer questions about the menu.",
            }
        ]
    )
    return thread.id

def generate_chat_response2(thread_id, text_transcript):
    """Generate a chat response based on the ongoing chat and menu using threads."""
    # Add user message to the thread
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=text_transcript
    )

    # Create a run for the given thread
    run = client.beta.threads.runs.create(
        thread_id=thread_id, 
        assistant_id='asst_VV46jnIhXY2RPOy4KVA39Qux'
    )

    # Poll the run until it completes
    run_id = run.id
    run = client.beta.threads.runs.poll(thread_id=thread_id, run_id=run_id)

    # Retrieve the latest message from the run
    response_messages = list(client.beta.threads.messages.list(thread_id=thread_id, run_id=run_id))
    response_message = response_messages[-1]  # Get the last message

    # Extract and return the text content from the response message
    return response_message.content[0].text.value

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    app.logger.debug(f"Received chat request data: {data}")
    
    user_message = data.get('message')
    thread_id = data.get('thread_id')
    
    if not user_message:
        app.logger.error('No message provided')
        return jsonify({'error': 'No message provided'}), 400

    if not thread_id:
        thread_id = initialize_thread2()

    try:
        reply = generate_chat_response2(thread_id, user_message)
        app.logger.debug(f"OpenAI response: {reply}")
        return jsonify({'response': reply, 'thread_id': thread_id})
    except Exception as e:
        app.logger.error(f'Error occurred while processing OpenAI API request: {e}')
        return jsonify({'error': str(e)}), 500

def get_thread_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT thread_id FROM user_threads WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result['thread_id'] if result else None

def save_thread_id(user_id, thread_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_threads (user_id, thread_id) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET thread_id = EXCLUDED.thread_id",
        (user_id, thread_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

@app.route('/webhook/lineChat', methods=['POST'])
def webhook():
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
                logging.debug(f"OpenAI response: {reply}")
                send_line_message(reply_token, reply)
            except Exception as e:
                logging.error(f'Error occurred while processing OpenAI API request: {e}')
                send_line_message(reply_token, "Sorry, I couldn't process your request.")

    return jsonify({'status': 'success'})

def send_line_message(reply_token, text):
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

def initialize_thread():
    try:
        thread = client.beta.threads.create(
            messages=[
                {
                    "role": "assistant",
                    "content": "You are an expert restaurant server. Provide short answers that properly answer questions and upsell items. "
                               "Do not include sources or citations in your responses. "
                               "Use your knowledge base to answer questions about the menu."
                               "Your responses should never be in markdown format"
                               "Your business hours are M-Sat 9am-5:30pm, Closed on Sundays"
                               "29/1130 Moo 12 , Nong Prue, Bang Lamung, Chonburi 20150 · Phone | +66 94 290 9196",
                }
            ]
        )
        return thread.id
    except Exception as e:
        logging.error(f"Failed to initialize thread: {e}")
        return None

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

    # Create a run for the given thread
    run = client.beta.threads.runs.create(
        thread_id=thread_id, 
        assistant_id='asst_VV46jnIhXY2RPOy4KVA39Qux'
    )

    # Poll the run until it completes
    run_id = run.id
    run = client.beta.threads.runs.poll(thread_id=thread_id, run_id=run_id)

    # Retrieve the latest message from the run
    response_messages = list(client.beta.threads.messages.list(thread_id=thread_id, run_id=run_id))
    response_message = response_messages[-1]  # Get the last message

    # Extract and return the text content from the response message
    return response_message.content[0].text.value

@app.route('/api/call-waiter', methods=['POST'])
def call_waiter():
    message = request.json.get('message', 'A customer is calling a waiter!')
    table_name = request.json.get('table_name', 'Unknown table')
    socketio.emit('call_waiter', {'message': message, 'table_name': table_name})
    return jsonify({'status': 'success'}), 200

# new addition
@app.route('/login')
def login():
    """Redirects the user to the LINE Login authorization URL."""
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

import os
from flask import request, redirect, jsonify, session
import requests

@app.route('/callback')
def callback():
    """Handles the callback from LINE after the user authorizes the app."""
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

    user_id = profile_data['userId']
    display_name = profile_data['displayName']

    # Check if the user already exists in the database using the LINE ID
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code FROM loyalty_accounts WHERE user_id = %s", (user_id,))
    existing_user = cursor.fetchone()

    if not existing_user:
        # If user doesn't exist, generate and store a new QR code
        new_code = generate_code()
        store_qr_code(user_id, new_code, display_name)

    cursor.close()
    conn.close()


    frontend_url = 'https://qultureloungemenudevelopment.netlify.app/line-login'  # Replace with your production URL

    # Redirect to the appropriate frontend URL with the user ID
    return redirect(f'{frontend_url}?user_id={user_id}')


def generate_code(length=10):
    """Generates a random alphanumeric code."""
    return ''.join(os.urandom(10).hex().upper()[:length])

import qrcode
from io import BytesIO
from PIL import Image

def generate_and_save_qr_code(data):
    # Generate QR Code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # Create an image from the QR Code instance
    img = qr.make_image(fill_color="black", back_color="white")

    # Save the image to a BytesIO object (in memory)
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, 'PNG')  # Save without using the 'format' keyword

    # Return the BytesIO object or save it to the database
    img_byte_arr = img_byte_arr.getvalue()
    return img_byte_arr


def store_qr_code(user_id, code, display_name):
    qr_data = generate_and_save_qr_code(code)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO loyalty_accounts (user_id, code, qr_code_image, display_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
            code = EXCLUDED.code,
            qr_code_image = EXCLUDED.qr_code_image,
            display_name = EXCLUDED.display_name
            """,
            (user_id, code, psycopg2.Binary(qr_data), display_name)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Error storing QR code: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

@app.route('/api/get_qr_code/<user_id>', methods=['GET'])
def get_qr_code(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Retrieve the QR code image for the user
        cursor.execute("SELECT qr_code_image FROM loyalty_accounts WHERE user_id = %s", (user_id,))
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

# Catch-all route to serve the React frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    print(f"Caught path: {path}")
    return send_from_directory('build', 'index.html')
# new addition

if __name__ == '__main__':
    logging.debug("Starting Flask application")
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))