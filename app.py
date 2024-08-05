import json
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import paypalrestsdk
from functools import wraps
import csv
import io
import os
import requests
from datetime import datetime
import uuid
import yaml


app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'

# File paths for storing data
ITEMS_FILE = 'items.json'
TRANSACTION_FILE = 'transactions.json'
BANS_FILE = 'bans.json'
TEMPLATES_DIR = 'templates'

ITEMS = []
TRANSACTION_LOGS = []
BANS = []

with open('config.yml', 'r') as config_file:
    config = yaml.safe_load(config_file)

ADMIN_USERNAME = config['account']['username']
ADMIN_PASSWORD = config['account']['password']

paypalrestsdk.configure({
    "mode": config['paypal']['mode'],
    "client_id": config['paypal']['client_id'],
    "client_secret": config['paypal']['client_secret']
})

def load_items():
    global ITEMS
    try:
        with open(ITEMS_FILE, 'r', encoding='utf-8') as file:
            ITEMS = json.load(file)
            if not isinstance(ITEMS, list):
                ITEMS = []
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("Error loading items:", e)
        ITEMS = []

def save_items():
    with open(ITEMS_FILE, 'w', encoding='utf-8') as file:
        json.dump(ITEMS, file, ensure_ascii=False, indent=4)

def load_transactions():
    global TRANSACTION_LOGS
    try:
        with open(TRANSACTION_FILE, 'r', encoding='utf-8') as file:
            TRANSACTION_LOGS = json.load(file)
            if not isinstance(TRANSACTION_LOGS, list):
                TRANSACTION_LOGS = []

            # Convert string timestamps to datetime objects
            for transaction in TRANSACTION_LOGS:
                if isinstance(transaction['timestamp'], str):
                    transaction['timestamp'] = datetime.fromisoformat(transaction['timestamp'])

    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("Error loading transactions:", e)
        TRANSACTION_LOGS = []

def save_transactions():
    with open(TRANSACTION_FILE, 'w', encoding='utf-8') as file:
        json.dump(TRANSACTION_LOGS, file, default=str, ensure_ascii=False, indent=4)

def load_bans():
    global BANS
    try:
        with open(BANS_FILE, 'r', encoding='utf-8') as file:
            BANS = json.load(file)
            if not isinstance(BANS, list):
                BANS = []
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("Error loading bans:", e)
        BANS = []

def save_bans():
    with open(BANS_FILE, 'w', encoding='utf-8') as file:
        json.dump(BANS, file, ensure_ascii=False, indent=4)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('You need to enter your username to access the store.', 'warning')
            return redirect(url_for('user_login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('You need to be logged in as admin to access this page.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        valid_tokens = config['api']['tokens']

        if not token or token not in valid_tokens:
            return jsonify(success=False, message="Token is missing or invalid"), 401

        return f(*args, **kwargs)
    return decorated_function

def getUuid(username):
    try:
        url = f"https://playerdb.co/api/player/minecraft/{username}"
        response = requests.get(url)
        data = response.json()
        if data["success"]:
            return data["data"]["player"]["id"]
        else:
            print(f"Failed to fetch UUID for {username}: {data['message']}")
    except Exception as e:
        print(f"Error fetching UUID: {e}")
    return None

def generate_transaction_id():
    return str(uuid.uuid4())

@app.route('/user_login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form.get('username')
        if username:
            session['username'] = username
            return redirect(url_for('index'))
    return render_template('user_login.html')

@app.route('/user_logout')
@login_required
def user_logout():
    session.pop('username', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('user_login'))

@app.route('/')
@login_required
def index():
    selected_category = request.args.get('category')
    if selected_category:
        items = [item for item in ITEMS if item['category'] == selected_category]
    else:
        items = ITEMS

    # Get recent purchases
    recent_purchases = sorted(TRANSACTION_LOGS, key=lambda x: x['timestamp'], reverse=True)[:5]

    # Update item prices based on active sales
    items_with_prices = [{
        **item,
        "current_price": item['price']
    } for item in items]

    return render_template('index.html', items=items_with_prices, categories={item['category'] for item in ITEMS}, selected_category=selected_category, recent_purchases=recent_purchases)

@app.route('/add_to_cart/<int:item_id>')
@login_required
def add_to_cart(item_id):
    try:
        # Get the cart from the session, default to an empty dictionary if not present
        cart = session.get('cart', {})

        # Find the item by ID
        item = next((item for item in ITEMS if item['id'] == item_id), None)
        if not item:
            return jsonify(success=False, message="Item not found.")

        # Ensure item IDs are strings in the cart
        item_id_str = str(item_id)

        # Retrieve purchase limit from the item data
        purchase_limit = item.get('purchase_limit', 1)  # Default to 5 if no limit is specified

        # Check if the item is already in the cart
        if item_id_str in cart:
            # Check if the purchase limit is reached
            if cart[item_id_str] >= purchase_limit:
                return jsonify(success=False, message="Purchase limit reached for this item.")
            # Increment the quantity
            cart[item_id_str] += 1
        else:
            # Add the item with a quantity of 1
            cart[item_id_str] = 1

        # Update the session cart
        session['cart'] = cart
        session.modified = True
        return jsonify(success=True, message="Item added to cart!")
    except Exception as e:
        print("Error adding item to cart:", e)
        return jsonify(success=False, message="An error occurred while adding the item to the cart.")


@app.route('/remove_one_from_cart/<int:item_id>', methods=['GET'])
@login_required
def remove_one_from_cart(item_id):
    cart = session.get('cart', {})
    item_id_str = str(item_id)
    if item_id_str in cart:
        cart[item_id_str] -= 1
        if cart[item_id_str] <= 0:
            del cart[item_id_str]
        session['cart'] = cart
        session.modified = True
    return jsonify(success=True)

@app.route('/remove_from_cart/<int:item_id>', methods=['GET'])
@login_required
def remove_from_cart(item_id):
    cart = session.get('cart', {})
    item_id_str = str(item_id)
    if item_id_str in cart:
        del cart[item_id_str]
        session['cart'] = cart
        session.modified = True
    return jsonify(success=True)

@app.route('/cart')
@login_required
def cart():
    cart_items = session.get('cart', {})
    
    # Create a mapping of item_id to item details for easy lookup
    items_dict = {item['id']: item for item in ITEMS}
    
    return render_template('cart.html', cart=cart_items, items_dict=items_dict)

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    username = session.get('username')
    if not username:
        return redirect(url_for('cart'))

    cart_items = [item for item in ITEMS if item['id'] in session.get('cart', [])]
    total = sum(item['price'] for item in cart_items)

    # Generate a unique transaction ID
    transaction_id = generate_transaction_id()

    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {
            "payment_method": "paypal"},
        "redirect_urls": {
            "return_url": url_for('payment_success', _external=True),
            "cancel_url": url_for('cart', _external=True)},
        "transactions": [{
            "item_list": {
                "items": [{
                    "name": item['name'],
                    "sku": item['id'],
                    "price": str(item['price']),
                    "currency": "USD",
                    "quantity": 1} for item in cart_items]},
            "amount": {
                "total": str(total),
                "currency": "USD"},
            "description": f"Purchase by {username}.",
            "custom": transaction_id  # Include the transaction ID here
        }]
    })

    if payment.create():
        for link in payment.links:
            if link.method == "REDIRECT":
                redirect_url = link.href
                # Log transaction (mock)
                TRANSACTION_LOGS.append({
                    "transaction_id": transaction_id,  # Log the transaction ID
                    "username": username,
                    "amount": total,
                    "status": "Pending",
                    "items": cart_items,
                    "timestamp": datetime.now()
                })
                return redirect(redirect_url)
    else:
        return "Payment failed."

@app.route('/payment/success')
@login_required
def payment_success():
    payment_id = request.args.get('paymentId')
    payer_id = request.args.get('PayerID')

    payment = paypalrestsdk.Payment.find(payment_id)

    if payment.execute({"payer_id": payer_id}):
        # Retrieve the transaction ID from the PayPal payment details
        transaction_id = payment.transactions[0].custom

        # Find the transaction in your logs and mark it as completed
        for t in TRANSACTION_LOGS:
            if t['transaction_id'] == transaction_id:
                t['status'] = 'Completed'
                break

        session.pop('cart', None)
        return render_template('success.html')
    else:
        return "Payment failed."

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
@admin_login_required
def admin_dashboard():
    total_payments = sum(t['amount'] for t in TRANSACTION_LOGS if t['status'] == 'Pending')
    total_transactions = len(TRANSACTION_LOGS)
    total_bans = len(BANS)

    # Generate sales data for the chart
    sales_data = {
        'labels': [],
        'values': []
    }

    # Aggregate sales data by date
    sales_by_date = {}
    for transaction in TRANSACTION_LOGS:
        if transaction['status'] == 'Pending':
            date_str = transaction['timestamp'].strftime('%Y-%m-%d')
            sales_by_date[date_str] = sales_by_date.get(date_str, 0) + transaction['amount']

    for date, total in sorted(sales_by_date.items()):
        sales_data['labels'].append(date)
        sales_data['values'].append(total)

    return render_template('admin_dashboard.html',
                           total_payments=total_payments,
                           total_transactions=total_transactions,
                           total_bans=total_bans,
                           sales_data=sales_data)

@app.route('/admin/items')
@admin_login_required
def admin_items():
    return render_template('admin_items.html', items=ITEMS)

@app.route('/admin/transactions')
@admin_login_required
def admin_transactions():
    print(TRANSACTION_LOGS)  # Debug: Print the structure to verify
    return render_template('admin_transactions.html', transactions=TRANSACTION_LOGS, items=ITEMS)

@app.route('/admin/bans')
@admin_login_required
def admin_bans():
    return render_template('admin_bans.html', bans=BANS)

@app.route('/admin/add_item', methods=['POST'])
@admin_login_required
def add_item():
    item_name = request.form.get('name')
    item_price = float(request.form.get('price'))
    item_limit = float(request.form.get('limit'))
    item_category = request.form.get('category')
    item_description = request.form.get('description')
    item_image = request.form.get('image')
    item_command = request.form.get('command')

    item_id = max(item['id'] for item in ITEMS) + 1 if ITEMS else 1
    new_item = {
        "id": item_id,
        "name": item_name,
        "price": item_price,
        "category": item_category,
        "description": item_description,
        "image": item_image,
        "purchase_limit": item_limit,
        "command": item_command
    }
    ITEMS.append(new_item)
    save_items()  # Save items after adding a new one
    return jsonify(success=True, message=f'Item {item_name} added successfully!')

@app.route('/admin/edit_item/<int:item_id>', methods=['GET', 'POST'])
@admin_login_required
def edit_item(item_id):
    item = next((item for item in ITEMS if item['id'] == item_id), None)
    if request.method == 'POST':
        if item:
            item['name'] = request.form.get('name')
            item['price'] = float(request.form.get('price'))
            item['purchase_limit'] = float(request.form.get('limit'))
            item['category'] = request.form.get('category')
            item['description'] = request.form.get('description')
            item['image'] = request.form.get('image')
            save_items()  # Save items after editing
            return jsonify(success=True, message=f'Item {item["name"]} updated successfully!')
        return jsonify(success=False, message='Item not found.')
    return render_template('edit_item.html', item=item)

@app.route('/admin/delete_item/<int:item_id>', methods=['POST'])
@admin_login_required
def delete_item(item_id):
    global ITEMS
    ITEMS = [item for item in ITEMS if item['id'] != item_id]
    save_items()  # Save items after deleting
    return jsonify(success=True, message='Item deleted successfully.')

@app.route('/admin/ban_user', methods=['POST'])
@admin_login_required
def ban_user():
    username = request.form.get('username')
    if username and username not in BANS:
        BANS.append(username)
        save_bans()  # Save bans after adding a new one
        return jsonify(success=True, message=f'User {username} banned successfully!')
    return jsonify(success=False, message='User already banned or invalid username.')

@app.route('/admin/unban_user', methods=['POST'])
@admin_login_required
def unban_user():
    username = request.form.get('username')
    if username in BANS:
        BANS.remove(username)
        save_bans()  # Save bans after removing one
        return jsonify(success=True, message=f'User {username} unbanned successfully!')
    return jsonify(success=False, message='User not found in banned list.')

@app.route('/admin/add_transaction', methods=['POST'])
@admin_login_required
def add_transaction():
    username = request.form.get('username')
    amount = float(request.form.get('amount'))
    status = request.form.get('status')
    selected_items = request.form.getlist('items[]')

    # Find items by name and get their details
    items = [{"name": item["name"], "price": item["price"]} for item in ITEMS if item["name"] in selected_items]

    transaction = {
        "transaction_id": generate_transaction_id(),
        "uuid": getUuid(username),
        "username": username,
        "amount": amount,
        "status": status,
        "items": items,
        "timestamp": datetime.now()
    }
    TRANSACTION_LOGS.append(transaction)
    save_transactions()  # Save transactions after adding a new one
    return jsonify(success=True, message=f'Transaction for {username} added successfully!')

@app.route('/admin/delete_transaction', methods=['POST'])
@admin_login_required
def delete_transaction():
    index = int(request.form.get('index'))
    if 0 <= index < len(TRANSACTION_LOGS):
        transaction = TRANSACTION_LOGS.pop(index)
        save_transactions()  # Save updated transactions to file
        return jsonify(success=True, message=f'Transaction for {transaction["username"]} deleted successfully.')
    return jsonify(success=False, message='Transaction not found.')

@app.route('/admin/export_transactions', methods=['GET'])
@admin_login_required
def export_transactions():
    output = io.StringIO()
    writer = csv.writer(output)

    # Write CSV header
    writer.writerow(['Username', 'Amount', 'Status', 'Items'])

    # Write CSV rows
    for transaction in TRANSACTION_LOGS:
        items = ', '.join([item['name'] for item in transaction['items']])
        writer.writerow([transaction['username'], transaction['amount'], transaction['status'], items])

    output.seek(0)
    return jsonify(success=True, data=output.getvalue())

@app.route('/admin/template_editor_api', methods=['GET', 'POST'])
@admin_login_required
def template_editor():
    if request.method == 'GET':
        template_name = request.args.get('template')
        if not template_name:
            return jsonify(success=False, message="No template specified.")

        try:
            with open(os.path.join(TEMPLATES_DIR, template_name), 'r', encoding='utf-8') as file:
                template_content = file.read()
            return jsonify(success=True, template_content=template_content)
        except FileNotFoundError:
            return jsonify(success=False, message="Template not found.")

    if request.method == 'POST':
        template_name = request.form.get('template_name')
        template_content = request.form.get('template_content')

        if not template_name:
            return jsonify(success=False, message="No template specified.")

        try:
            with open(os.path.join(TEMPLATES_DIR, template_name), 'w', encoding='utf-8') as file:
                file.write(template_content)
            return jsonify(success=True, message="Template saved successfully.")
        except Exception as e:
            return jsonify(success=False, message=str(e))

@app.route('/admin/template_editor')
@admin_login_required
def template_editor_page():
    templates = os.listdir(TEMPLATES_DIR)
    return render_template('template_editor.html', templates=templates)

def get_user_spending():
    user_spending = {}
    for transaction in TRANSACTION_LOGS:
        if transaction['status'] == 'Completed':
            username = transaction['username']
            amount = transaction['amount']
            if username in user_spending:
                user_spending[username] += amount
            else:
                user_spending[username] = amount
    return user_spending

# API to retrieve the 5 most recent purchases
@app.route('/api/recent_purchases', methods=['GET'])
@token_required
def recent_purchases():
    # Filter transactions for completed ones
    completed_transactions = [t for t in TRANSACTION_LOGS if t['status'] == 'Completed']
    # Sort transactions by timestamp in descending order
    sorted_transactions = sorted(completed_transactions, key=lambda t: t['timestamp'], reverse=True)
    # Get the 5 most recent purchases
    recent_purchases = sorted_transactions[:5]
    return jsonify(success=True, purchases=recent_purchases)

# API to retrieve the top 5 users by spending for the current month
@app.route('/api/top_spenders', methods=['GET'])
@token_required
def top_spenders():
    # Calculate the first day of the current month
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)

    # Filter transactions for the current month and completed ones
    monthly_transactions = [
        t for t in TRANSACTION_LOGS
        if t['timestamp'] >= start_of_month and t['status'] == 'Completed'
    ]

    # Calculate total spending per user
    user_spending = {}
    for transaction in monthly_transactions:
        username = transaction['username']
        amount = transaction['amount']
        if username in user_spending:
            user_spending[username] += amount
        else:
            user_spending[username] = amount

    # Sort users by total spending in descending order and get the top 5
    top_spenders = sorted(user_spending.items(), key=lambda x: x[1], reverse=True)[:5]
    return jsonify(success=True, top_spenders=top_spenders)

@app.route('/api/pending_purchases', methods=['GET'])
@token_required
def get_pending_purchases():
    # Return only transaction IDs of pending purchases
    pending_purchases = [
        t['transaction_id']
        for t in TRANSACTION_LOGS if t['status'] == 'Completed' and not t.get('fulfilled', False)
    ]
    return jsonify(pending_purchases)

@app.route('/api/transaction_details/<string:transaction_id>', methods=['GET'])
@token_required
def get_transaction_details(transaction_id):
    # Fetch details of a specific transaction
    transaction = next((t for t in TRANSACTION_LOGS if t['transaction_id'] == transaction_id), None)
    if transaction:
        return jsonify({
            "transaction_id": transaction['transaction_id'],
            "username": transaction['username'],
            "items": transaction['items'],
            "timestamp": transaction['timestamp'].isoformat()
        })
    return jsonify(success=False, message="Transaction not found."), 404

@app.route('/api/mark_fulfilled', methods=['POST'])
@token_required
def mark_fulfilled():
    data = request.json
    transaction_id = data.get('transaction_id')

    for t in TRANSACTION_LOGS:
        if t['transaction_id'] == transaction_id:
            t['fulfilled'] = True
            save_transactions()
            return jsonify(success=True, message="Transaction marked as fulfilled.")

    return jsonify(success=False, message="Transaction not found.")

@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    load_items()
    load_transactions()
    load_bans()
    app.run(debug=False)
