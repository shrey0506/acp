import os

def create_file(path, content):
    dir_name = os.path.dirname(path)
    if dir_name: os.makedirs(dir_name, exist_ok=True)
    with open(path, 'w') as f: f.write(content.strip() + '\n')
    print(f"Updated: {path}")

# --- 1. Protocol Update (Adding BANK_CHAT and OVERRIDE) ---
create_file("src/protocols/acp_protocol.py", """
import uuid, datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any

class ACPIntent(Enum):
    INITIATE_APPLICATION = "INITIATE_APPLICATION"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    PROVIDE_INFORMATION = "PROVIDE_INFORMATION"
    NEGOTIATE_TERMS = "NEGOTIATE_TERMS"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    BANK_CHAT = "BANK_CHAT"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"

class ApplicationState(Enum):
    INFO_REQUESTED = "INFO_REQUESTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

@dataclass
class ACPMessage:
    sender_id: str
    receiver_id: str
    intent: ACPIntent
    session_id: str
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

@dataclass
class ACPResponse:
    success: bool
    state: ApplicationState
    message: str
    payload: Dict[str, Any]
""")

# --- 2. Bank Agent Update (Handles Overrides & Chat) ---
create_file("src/agents/bank_agent.py", """
import json
from src.protocols.acp_protocol import ACPMessage, ACPResponse, ACPIntent, ApplicationState
from src.utils.llm_client import llm_client

class BankAgent:
    def __init__(self, bus):
        self.bus = bus
        self.records = {}
        self.REQUIRED_FIELDS = ["annual_income", "property_value", "credit_score", "employer_name"]

    def handle_message(self, msg: ACPMessage) -> ACPResponse:
        sid = msg.session_id
        if sid not in self.records: self.records[sid] = {"data": {}, "offer": None, "floors": {}, "status": "Started", "chat": []}
        record = self.records[sid]

        if msg.intent == ACPIntent.BANK_CHAT:
            record["chat"].append({"sender": msg.payload.get("sender", "User"), "text": msg.payload["text"]})
            return ACPResponse(True, ApplicationState.UNDER_REVIEW, "Ack", {})

        if msg.intent in [ACPIntent.INITIATE_APPLICATION, ACPIntent.PROVIDE_INFORMATION]:
            record["data"].update(msg.payload)
            missing = [f for f in self.REQUIRED_FIELDS if f not in record["data"] or not record["data"][f]]
            if missing:
                record["status"] = "Waiting on Customer Info"
                return ACPResponse(False, ApplicationState.INFO_REQUESTED, "Missing data", {"missing_fields": missing})
            
            inc, prop = float(record["data"]["annual_income"]), float(record["data"]["property_value"])
            max_loan = min(inc * 4.5, prop * 0.90)
            record["floors"] = {"max_loan": max_loan, "max_term": 35}
            req_loan = float(record["data"].get("loan_amount", max_loan))
            
            offer = {"amount": min(req_loan, max_loan), "term": 25, "rate": 4.29, "monthly": self._calc_pmt(min(req_loan, max_loan), 25, 4.29)}
            record["offer"] = offer
            record["status"] = "Offer Made"
            return ACPResponse(True, ApplicationState.APPROVED, "Approved", {"offer": offer})

        elif msg.intent == ACPIntent.NEGOTIATE_TERMS:
            ask = msg.payload
            floors = record["floors"]
            prompt = f"Bank LLM. Customer asks: {ask}. Floors: MaxLoan {floors['max_loan']}. Cap if exceeded. Return JSON: {{'adjusted_loan': float, 'adjusted_term': int, 'concession': 'str'}}"
            try: dec = json.loads(llm_client.chat([{"role": "user", "content": prompt}], json_mode=True).content)
            except: dec = {"adjusted_loan": floors['max_loan'], "adjusted_term": 25, "concession": "System limit"}
            
            offer = {"amount": dec["adjusted_loan"], "term": dec.get("adjusted_term", 25), "rate": 4.29, "monthly": self._calc_pmt(dec["adjusted_loan"], dec.get("adjusted_term", 25), 4.29)}
            record["offer"] = offer
            record["status"] = "Negotiating"
            return ACPResponse(True, ApplicationState.APPROVED, dec.get("concession", ""), {"offer": offer})

        elif msg.intent == ACPIntent.ACCEPT_OFFER:
            record["status"] = "Deal Agreed"
            return ACPResponse(True, ApplicationState.APPROVED, "Agreed", {"offer": record["offer"]})

    def manual_action(self, sid: str, action: str, text: str, loan: float = None):
        record = self.records[sid]
        record["chat"].append({"sender": "Bank Human", "text": text})
        
        if action == "CHAT":
            self.bus.send(ACPMessage("BANK", "CUSTOMER", ACPIntent.BANK_CHAT, sid, {"text": text}))
        elif action == "OVERRIDE":
            record["offer"]["amount"] = float(loan)
            record["offer"]["monthly"] = self._calc_pmt(float(loan), record["offer"]["term"], record["offer"]["rate"])
            record["status"] = "Manually Overridden"
            self.bus.send(ACPMessage("BANK", "CUSTOMER", ACPIntent.MANUAL_OVERRIDE, sid, {"text": text, "offer": record["offer"]}))

    def _calc_pmt(self, amt, term, rate):
        r = (rate/100)/12
        return round(amt * (r * (1+r)**(term*12)) / ((1+r)**(term*12) - 1), 2)
""")

# --- 3. Customer Agent Update (Handles Incoming Chat) ---
create_file("src/agents/customer_agent.py", """
import uuid, json, re
from src.protocols.acp_protocol import ACPMessage, ACPResponse, ACPIntent, ApplicationState
from src.utils.llm_client import llm_client

class CustomerAgent:
    def __init__(self, bus, df):
        self.bus = bus
        self.df = df
        self.sessions = {}

    def handle_message(self, msg: ACPMessage) -> ACPResponse:
        sid = msg.session_id
        if msg.intent == ACPIntent.BANK_CHAT:
            self._add_msg(sid, "Bank Human", msg.payload["text"])
        elif msg.intent == ACPIntent.MANUAL_OVERRIDE:
            self.sessions[sid]["offer"] = msg.payload.get("offer")
            self._add_msg(sid, "Bank Human", f"OVERRIDE: {msg.payload.get('text')}")
            o = self.sessions[sid]["offer"]
            self._add_msg(sid, "Agent", f"The bank has manually overridden the offer: £{o['amount']:,.2f} over {o['term']} years. Monthly: £{o['monthly']:,.2f}.")
        return ACPResponse(True, ApplicationState.UNDER_REVIEW, "Ack", {})

    def start_session(self, customer_id: str):
        # Sanitize cust001 -> CUST00001
        m = re.match(r'cust(\d+)', customer_id.lower())
        if m: customer_id = f"CUST{int(m.group(1)):05d}"
        else: customer_id = customer_id.upper()

        sid = str(uuid.uuid4())
        try: profile = self.df[self.df['customer_id'] == customer_id].iloc[0].to_dict()
        except: return None # Invalid user

        # Synthesize a name if none exists
        name = f"User {int(customer_id.replace('CUST',''))}"
        
        limited_payload = {
            "annual_income": profile["annual_income"], "property_value": profile["property_value"],
            "credit_score": profile["credit_score"], "loan_amount": float(profile["property_value"]) * 0.85
        }
        
        self.sessions[sid] = {"customer_id": customer_id, "name": name, "chat": [], "state": "started", "missing_fields": [], "offer": None}
        
        self._add_msg(sid, "Agent", f"Welcome {name}! How can I help you today?")
        self._add_msg(sid, "Agent", "I am submitting your basic profile to the bank now to see what we can secure...")
        
        res = self.bus.send(ACPMessage(customer_id, "BANK", ACPIntent.INITIATE_APPLICATION, sid, limited_payload))
        self._handle_bank_res(sid, res)
        return sid

    def chat(self, sid: str, text: str):
        self._add_msg(sid, "User", text)
        sess = self.sessions[sid]
        
        # Sync chat to bank so human can see it
        self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.BANK_CHAT, sid, {"text": text, "sender": sess["name"]}))
        
        prompt = f"Customer says: '{text}'. Current missing fields: {sess['missing_fields']}. Current offer: {sess['offer']}. Intent: Extract missing info, OR counter-offer, OR accept. Return JSON: {{'action': 'PROVIDE_INFO'|'COUNTER'|'ACCEPT'|'CHAT', 'extracted_info': {{key:val}}, 'counter_amount': float, 'msg_to_user': 'str'}}"
        
        try: dec = json.loads(llm_client.chat([{"role": "user", "content": prompt}], json_mode=True).content)
        except: dec = {"action": "CHAT", "msg_to_user": "I am processing that."}

        if dec.get("msg_to_user"): self._add_msg(sid, "Agent", dec["msg_to_user"])

        if dec.get("action") == "PROVIDE_INFO":
            res = self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.PROVIDE_INFORMATION, sid, dec.get("extracted_info", {})))
            self._handle_bank_res(sid, res)
        elif dec.get("action") == "COUNTER":
            res = self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.NEGOTIATE_TERMS, sid, {"loan_amount": dec.get("counter_amount")}))
            self._handle_bank_res(sid, res)
        elif dec.get("action") == "ACCEPT":
            self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.ACCEPT_OFFER, sid, {}))
            self._add_msg(sid, "Agent", "Excellent. You have accepted the bank's offer. The process is complete.")

    def _handle_bank_res(self, sid, res):
        sess = self.sessions[sid]
        if res.state == ApplicationState.INFO_REQUESTED:
            sess["missing_fields"] = res.payload.get("missing_fields", [])
            self._add_msg(sid, "Agent", f"The bank requires more information to proceed. Could you please provide your: {', '.join(sess['missing_fields']).replace('_', ' ')}?")
        elif res.state == ApplicationState.APPROVED:
            sess["offer"] = res.payload.get("offer")
            o = sess["offer"]
            self._add_msg(sid, "Agent", f"Good news! The bank made an offer: £{o['amount']:,.2f} over {o['term']} years at {o['rate']}%. Monthly repayment: £{o['monthly']:,.2f}. Do you want to Accept, or request a different amount?")

    def _add_msg(self, sid, sender, text):
        self.sessions[sid]["chat"].append({"sender": sender, "text": text})
""")

# --- 4. Customer UI Update (Login + Auto-Connect) ---
create_file("src/ui/customer_ui.py", """
from flask import Flask, jsonify, request, render_template_string
app = Flask("CustomerUI")
agent = None
def set_agent(a): global agent; agent = a

HTML = '''
<!DOCTYPE html>
<html><head>
<title>Lloyds Bank | Customer</title>
<style>
    :root { --lloyds-green: #006A4D; --lloyds-dark: #004B35; --bg: #F4F6F8; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); margin: 0; display: flex; height: 100vh; justify-content: center; align-items: center;}
    
    /* Login Overlay */
    #loginScreen { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); width: 320px; text-align: center; }
    #loginScreen h2 { color: var(--lloyds-green); margin-bottom: 20px; }
    #loginScreen input { width: 100%; padding: 12px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
    #loginScreen button { width: 100%; background: var(--lloyds-green); color: white; border: none; padding: 12px; border-radius: 6px; cursor: pointer; font-size: 1.1rem; }
    #errorMsg { color: red; font-size: 0.9rem; margin-top: 10px; display: none; }
    
    /* Chat UI */
    #mainApp { display: none; width: 100%; height: 100%; }
    .chat-container { flex: 1; display: flex; flex-direction: column; background: white; margin: 20px auto; max-width: 800px; width:100%; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden;}
    .header { background: var(--lloyds-green); color: white; padding: 20px; font-size: 1.2rem; font-weight: bold; }
    .messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
    .msg { max-width: 70%; padding: 12px 16px; border-radius: 8px; line-height: 1.4; }
    .msg.User { background: #E3E8ED; align-self: flex-end; border-bottom-right-radius: 0; }
    .msg.Agent { background: var(--lloyds-dark); color: white; align-self: flex-start; border-bottom-left-radius: 0; }
    .msg.Bank.Human { background: #8B0000; color: white; align-self: flex-start; border-bottom-left-radius: 0; font-weight: bold; }
    .input-area { display: flex; padding: 15px; border-top: 1px solid #eee; background: white;}
    .input-area input { flex: 1; padding: 12px; border: 1px solid #ccc; border-radius: 6px; outline: none; }
    .input-area button { background: var(--lloyds-green); color: white; border: none; padding: 0 20px; margin-left: 10px; border-radius: 6px; cursor: pointer; font-weight: bold;}
</style>
</head><body>
    
    <div id="loginScreen">
        <h2>Lloyds Secure Login</h2>
        <input type="text" id="userId" placeholder="User ID (e.g., cust001)">
        <input type="password" id="pwd" placeholder="Password (1234)" onkeypress="if(event.key === 'Enter') login()">
        <button onclick="login()">Log In</button>
        <div id="errorMsg">Invalid Credentials or User Not Found</div>
    </div>

    <div id="mainApp">
        <div class="chat-container">
            <div class="header">Lloyds Virtual Assistant</div>
            <div class="messages" id="msgs"></div>
            <div class="input-area">
                <input type="text" id="chatInput" placeholder="Type your message..." onkeypress="if(event.key === 'Enter') sendMsg()">
                <button onclick="sendMsg()">Send</button>
            </div>
        </div>
    </div>

    <script>
        let sid = null;
        async function login() {
            const uid = document.getElementById('userId').value;
            const pwd = document.getElementById('pwd').value;
            if(pwd !== '1234') { document.getElementById('errorMsg').style.display = 'block'; return; }
            
            const res = await fetch('/api/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cid: uid})});
            const data = await res.json();
            if(!data.sid) { document.getElementById('errorMsg').style.display = 'block'; return; }
            
            sid = data.sid;
            document.getElementById('loginScreen').style.display = 'none';
            document.getElementById('mainApp').style.display = 'flex';
            poll(); setInterval(poll, 1500);
        }
        async function sendMsg() {
            const el = document.getElementById('chatInput');
            if(!el.value || !sid) return;
            await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sid: sid, text: el.value})});
            el.value = ''; poll();
        }
        async function poll() {
            if(!sid) return;
            const res = await fetch('/api/poll?sid='+sid);
            const data = await res.json();
            const box = document.getElementById('msgs');
            box.innerHTML = data.chat.map(m => `<div class="msg ${m.sender.replace(' ', '.')}"><b>${m.sender}:</b> ${m.text}</div>`).join('');
            box.scrollTop = box.scrollHeight;
        }
    </script>
</body></html>
'''
@app.route('/')
def home(): return render_template_string(HTML)
@app.route('/api/start', methods=['POST'])
def start(): return jsonify({"sid": agent.start_session(request.json.get('cid'))})
@app.route('/api/chat', methods=['POST'])
def chat(): agent.chat(request.json.get('sid'), request.json.get('text')); return jsonify({"status": "ok"})
@app.route('/api/poll', methods=['GET'])
def poll(): return jsonify(agent.sessions.get(request.args.get('sid'), {"chat": []}))
""")

# --- 5. Bank UI Update (Enterprise 3-Column Layout with Overrides) ---
create_file("src/ui/bank_ui.py", """
from flask import Flask, jsonify, render_template_string, request
app = Flask("BankUI")
agent = None
def set_agent(a): global agent; agent = a

HTML = '''
<!DOCTYPE html>
<html><head>
<title>Lloyds | Command Center</title>
<style>
    :root { --bg: #0d1117; --panel: #161b22; --border: #30363d; --accent: #006A4D; --text: #c9d1d9; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin:0; display:flex; height: 100vh;}
    
    .sidebar { width: 300px; background: var(--panel); border-right: 1px solid var(--border); overflow-y: auto; padding: 20px;}
    .main { flex: 1; display: flex; flex-direction: column; padding: 20px;}
    .controls { width: 350px; background: var(--panel); border-left: 1px solid var(--border); padding: 20px;}
    
    .card { background: var(--bg); padding: 15px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; cursor: pointer; }
    .card:hover, .card.active { border-color: var(--accent); }
    
    .chat-box { flex:1; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; overflow-y: auto; margin-bottom: 20px;}
    .msg { margin-bottom: 10px; padding: 10px; border-radius: 6px; background: var(--bg); border: 1px solid var(--border);}
    .msg.Bank.Human { border-left: 4px solid red; }
    
    .input-grp { display: flex; margin-bottom: 10px;}
    input, button { padding: 10px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg); color: white;}
    input { flex: 1; margin-right: 10px;}
    button { background: var(--accent); cursor: pointer; font-weight: bold;}
    button.danger { background: #8B0000; }
</style>
</head><body>
    <div class="sidebar">
        <h3 style="color:var(--accent)">Active Sessions</h3>
        <div id="sessionList"></div>
    </div>
    
    <div class="main">
        <h3>Live Chat Feed</h3>
        <div class="chat-box" id="chatFeed">Select a session...</div>
        <div class="input-grp">
            <input type="text" id="bankChatMsg" placeholder="Send message as Bank Human..." onkeypress="if(event.key === 'Enter') sendAction('CHAT')">
            <button onclick="sendAction('CHAT')">Send Chat</button>
        </div>
    </div>
    
    <div class="controls">
        <h3 style="color:#8B0000">Manual Overrides</h3>
        <p style="font-size:0.9rem; color:#888;">Force an offer directly to the customer, bypassing LLM logic.</p>
        <div style="background:var(--bg); padding:15px; border-radius:8px; border: 1px solid var(--border);">
            <label>Override Loan Amount (£):</label>
            <input type="number" id="overrideLoan" style="width:100%; margin: 10px 0; box-sizing:border-box;">
            <input type="text" id="overrideReason" placeholder="Reason (e.g., Manager Approval)" style="width:100%; margin-bottom:10px; box-sizing:border-box;">
            <button class="danger" style="width:100%" onclick="sendAction('OVERRIDE')">FORCE OVERRIDE</button>
        </div>
        
        <h3 style="margin-top:30px;">Session Data</h3>
        <pre id="dataDump" style="font-size:0.8rem; overflow-x:auto;"></pre>
    </div>

    <script>
        let activeSid = null;
        let lastData = {};
        
        async function load() {
            const res = await fetch('/api/data');
            lastData = await res.json();
            
            document.getElementById('sessionList').innerHTML = Object.entries(lastData).map(([sid, d]) => `
                <div class="card ${sid === activeSid ? 'active' : ''}" onclick="selectSession('${sid}')">
                    <b>${d.data.customer_id || 'Unknown'}</b><br>
                    <small>${d.status}</small>
                </div>
            `).join('');
            
            if(activeSid && lastData[activeSid]) renderSession(activeSid);
        }
        
        function selectSession(sid) { activeSid = sid; renderSession(sid); load(); }
        
        function renderSession(sid) {
            const d = lastData[sid];
            document.getElementById('chatFeed').innerHTML = d.chat.map(m => `<div class="msg ${m.sender.replace(' ', '.')}"><b>${m.sender}:</b> ${m.text}</div>`).join('');
            document.getElementById('dataDump').innerText = JSON.stringify(d, null, 2);
        }
        
        async function sendAction(action) {
            if(!activeSid) return alert("Select a session first");
            const text = action === 'CHAT' ? document.getElementById('bankChatMsg').value : document.getElementById('overrideReason').value;
            const loan = action === 'OVERRIDE' ? document.getElementById('overrideLoan').value : null;
            
            await fetch('/api/action', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({sid: activeSid, action: action, text: text, loan: loan})
            });
            document.getElementById('bankChatMsg').value = '';
            load();
        }
        setInterval(load, 2000); load();
    </script>
</body></html>
'''
@app.route('/')
def home(): return render_template_string(HTML)
@app.route('/api/data')
def get_data(): return jsonify(agent.records)
@app.route('/api/action', methods=['POST'])
def action(): 
    d = request.json
    agent.manual_action(d['sid'], d['action'], d['text'], d.get('loan'))
    return jsonify({"status": "ok"})
""")

# --- 6. Run Demo Update (Wire up the 2-way bus) ---
create_file("run_demo.py", """
import threading
from data.generate_5k import generate_5k_customers
from src.utils.message_bus import bus
from src.agents.bank_agent import BankAgent
from src.agents.customer_agent import CustomerAgent
from src.ui.customer_ui import app as customer_app, set_agent as set_customer_agent
from src.ui.bank_ui import app as bank_app, set_agent as set_bank_agent

if __name__ == "__main__":
    df = generate_5k_customers()
    
    # Wire the 2-way bus
    bank_agent = BankAgent(bus)
    customer_agent = CustomerAgent(bus, df)
    
    bus.register("BANK", bank_agent.handle_message)
    bus.register("CUSTOMER", customer_agent.handle_message)
    
    set_bank_agent(bank_agent)
    set_customer_agent(customer_agent)

    threading.Thread(target=lambda: bank_app.run(port=5002, debug=False, use_reloader=False), daemon=True).start()
    
    print("\\n=== SYSTEM ONLINE ===")
    print("CUSTOMER LOGIN: http://localhost:5001  (Use cust001, password 1234)")
    print("BANK DASHBOARD: http://localhost:5002")
    
    customer_app.run(port=5001, debug=False, use_reloader=False)
""")

print("\nLogin & Bank Override Update Applied! Run `python3 run_demo.py` to start.")