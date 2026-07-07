import os

def create_file(path, content):
    dir_name = os.path.dirname(path)
    if dir_name: os.makedirs(dir_name, exist_ok=True)
    with open(path, 'w') as f: f.write(content.strip() + '\n')
    print(f"Updated: {path}")

# --- 1. Protocols ---
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

# --- 2. Utils (Robust LLM Client) ---
create_file("src/utils/llm_client.py", """
import json, os, re
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ACP")
load_dotenv()

class LiteLLMClient:
    def __init__(self):
        self.model = os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash-lite")
        self.enabled = bool(os.getenv("GEMINI_API_KEY"))
        if self.enabled: import litellm; self.litellm = litellm

    def chat(self, messages, json_mode=False):
        if not self.enabled: return {"action": "CHAT", "msg_to_user": "Fallback enabled"}
        
        kwargs = {"model": self.model, "messages": messages, "temperature": 0.1}
        try:
            res = self.litellm.completion(**kwargs).choices[0].message.content
            if not json_mode: return res
            
            # Robust JSON extraction
            match = re.search(r'\\{.*\\}', res.replace('\\n', ' '), re.DOTALL)
            if match: return json.loads(match.group(0))
            return json.loads(res)
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return {"action": "CHAT", "msg_to_user": "Processing error, please try again."}

llm_client = LiteLLMClient()
""")

create_file("src/utils/message_bus.py", """
import logging
logger = logging.getLogger("ACP")

class InMemoryBus:
    def __init__(self): self.agents = {}
    def register(self, agent_id, handler_fn): self.agents[agent_id] = handler_fn
    def send(self, message):
        if message.receiver_id in self.agents: return self.agents[message.receiver_id](message)
        raise Exception(f"Agent {message.receiver_id} not registered.")
bus = InMemoryBus()
""")

# --- 3. Bank Agent ---
create_file("src/agents/bank_agent.py", """
from src.protocols.acp_protocol import ACPMessage, ACPResponse, ACPIntent, ApplicationState
from src.utils.llm_client import llm_client

class BankAgent:
    def __init__(self, bus):
        self.bus = bus
        self.records = {}
        self.REQUIRED_FIELDS = ["annual_income", "property_value", "credit_score", "employer_name"]
        self.RATE_FLOOR = 3.99

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
                record["status"] = "Waiting on Info"
                return ACPResponse(False, ApplicationState.INFO_REQUESTED, "Missing data", {"missing_fields": missing})
            
            inc, prop = float(record["data"]["annual_income"]), float(record["data"]["property_value"])
            max_loan = min(inc * 4.5, prop * 0.90)
            record["floors"] = {"max_loan": max_loan, "max_term": 35, "best_rate": self.RATE_FLOOR}
            
            req_loan = float(record["data"].get("loan_amount", max_loan))
            starting_rate = 4.99 if int(record["data"].get("credit_score", 0)) < 700 else 4.29
            
            offer = {"amount": min(req_loan, max_loan), "term": 25, "rate": starting_rate, "monthly": self._calc_pmt(min(req_loan, max_loan), 25, starting_rate)}
            record["offer"] = offer
            record["status"] = "Offer Made"
            return ACPResponse(True, ApplicationState.APPROVED, "Approved", {"offer": offer})

        elif msg.intent == ACPIntent.NEGOTIATE_TERMS:
            ask = msg.payload
            floors = record["floors"]
            current_offer = record["offer"]
            
            prompt = f'''Customer asks for: Loan {ask.get('loan_amount', 0)}, Rate {ask.get('rate', current_offer['rate'])}%. Floors: MaxLoan {floors['max_loan']}, MinRate {floors['best_rate']}%. Cap if exceeded. Return JSON: {{"adjusted_loan": float, "adjusted_rate": float, "concession": "str"}}'''
            
            dec = llm_client.chat([{"role": "user", "content": prompt}], json_mode=True)
            amt = min(dec.get("adjusted_loan", current_offer['amount']), floors['max_loan'])
            rate = max(dec.get("adjusted_rate", current_offer['rate']), floors['best_rate'])
            
            offer = {"amount": amt, "term": ask.get("term", 25), "rate": rate, "monthly": self._calc_pmt(amt, ask.get("term", 25), rate)}
            record["offer"] = offer
            record["status"] = "Negotiating"
            
            announcement = f"[Bank AI] Countering with £{offer['amount']:,.2f} at {offer['rate']}%. Reason: {dec.get('concession', 'System generated')}"
            record["chat"].append({"sender": "Bank AI", "text": announcement})
            self.bus.send(ACPMessage("BANK", "CUSTOMER", ACPIntent.BANK_CHAT, sid, {"text": announcement, "sender": "Bank AI"}))
            
            return ACPResponse(True, ApplicationState.APPROVED, dec.get("concession", ""), {"offer": offer})

        elif msg.intent == ACPIntent.ACCEPT_OFFER:
            record["status"] = "Deal Agreed"
            return ACPResponse(True, ApplicationState.APPROVED, "Agreed", {"offer": record["offer"]})

    def manual_action(self, sid: str, action: str, text: str, loan: float = None):
        record = self.records[sid]
        record["chat"].append({"sender": "Bank Human", "text": text})
        
        if action == "CHAT":
            self.bus.send(ACPMessage("BANK", "CUSTOMER", ACPIntent.BANK_CHAT, sid, {"text": text, "sender": "Bank Human"}))
        elif action == "OVERRIDE":
            record["offer"]["amount"] = float(loan)
            record["offer"]["monthly"] = self._calc_pmt(float(loan), record["offer"]["term"], record["offer"]["rate"])
            record["status"] = "Manually Overridden"
            self.bus.send(ACPMessage("BANK", "CUSTOMER", ACPIntent.MANUAL_OVERRIDE, sid, {"text": text, "offer": record["offer"]}))

    def _calc_pmt(self, amt, term, rate):
        r = (rate/100)/12
        return round(amt * (r * (1+r)**(term*12)) / ((1+r)**(term*12) - 1), 2)
""")

# --- 4. Customer Agent ---
create_file("src/agents/customer_agent.py", """
import uuid, re
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
            self._add_msg(sid, msg.payload.get("sender", "Bank Human"), msg.payload["text"])
        elif msg.intent == ACPIntent.MANUAL_OVERRIDE:
            self.sessions[sid]["offer"] = msg.payload.get("offer")
            o = self.sessions[sid]["offer"]
            self._add_msg(sid, "Bank Human", f"OVERRIDE: {msg.payload.get('text')}")
            self._add_msg(sid, "Agent", f"The bank has manually overridden the offer: £{o['amount']:,.2f} over {o['term']} years. Monthly: £{o['monthly']:,.2f}.")
        return ACPResponse(True, ApplicationState.UNDER_REVIEW, "Ack", {})

    def start_session(self, customer_id: str):
        m = re.match(r'cust(\d+)', customer_id.lower())
        if m: customer_id = f"CUST{int(m.group(1)):05d}"
        else: customer_id = customer_id.upper()

        sid = str(uuid.uuid4())
        try: profile = self.df[self.df['customer_id'] == customer_id].iloc[0].to_dict()
        except: return None 

        name = f"User {int(customer_id.replace('CUST',''))}"
        
        self.sessions[sid] = {
            "customer_id": customer_id, "name": name, "chat": [], "state": "GREETING", 
            "missing_fields": [], "offer": None, "profile_payload": profile,
            "target_loan": None, "auto_rounds": 0
        }
        
        self._add_msg(sid, "Agent", f"Welcome {name}! I am your personal Lloyds assistant. How can I help you today?")
        return sid

    def chat(self, sid: str, text: str):
        self._add_msg(sid, "User", text)
        sess = self.sessions[sid]
        self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.BANK_CHAT, sid, {"text": text, "sender": sess["name"]}))
        
        prompt = f'''Customer says: "{text}". State: {sess['state']}. Missing: {sess['missing_fields']}. Offer: {sess['offer']}. 
        DECIDE ACTION:
        - If needing specific amount (e.g., "I need 120000"), action="START_MORTGAGE", target_loan=120000.
        - If providing requested info, action="PROVIDE_INFO", extracted_info={{key:val}}.
        - If they accept the offer, action="ACCEPT".
        - Else, action="CHAT".
        Return JSON: {{"action": "...", "extracted_info": {{}}, "target_loan": float, "msg_to_user": "str"}}'''
        
        dec = llm_client.chat([{"role": "user", "content": prompt}], json_mode=True)

        if dec.get("msg_to_user") and dec.get("action") != "START_MORTGAGE": 
            self._add_msg(sid, "Agent", dec["msg_to_user"])

        if dec.get("action") == "START_MORTGAGE":
            sess["state"] = "APPLYING"
            if dec.get("target_loan"): sess["target_loan"] = float(dec["target_loan"])
            
            payload = {
                "annual_income": sess["profile_payload"]["annual_income"], "property_value": sess["profile_payload"]["property_value"],
                "credit_score": sess["profile_payload"]["credit_score"], "loan_amount": sess.get("target_loan", 0)
            }
            self._add_msg(sid, "Agent", f"Noted. You are looking for £{sess.get('target_loan',0):,.2f}. Submitting your profile to the bank now...")
            res = self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.INITIATE_APPLICATION, sid, payload))
            self._handle_bank_res(sid, res)

        elif dec.get("action") == "PROVIDE_INFO":
            res = self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.PROVIDE_INFORMATION, sid, dec.get("extracted_info", {})))
            self._handle_bank_res(sid, res)

        elif dec.get("action") == "ACCEPT":
            self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.ACCEPT_OFFER, sid, {}))
            self._add_msg(sid, "Agent", "Excellent. You have accepted the bank's offer.")

    def _handle_bank_res(self, sid, res):
        sess = self.sessions[sid]
        if res.state == ApplicationState.INFO_REQUESTED:
            sess["missing_fields"] = res.payload.get("missing_fields", [])
            self._add_msg(sid, "Agent", f"The bank requires more information. Could you please provide your: {', '.join(sess['missing_fields']).replace('_', ' ')}?")
            
        elif res.state == ApplicationState.APPROVED:
            sess["offer"] = res.payload.get("offer")
            sess["auto_rounds"] += 1
            o = sess["offer"]
            
            if sess["auto_rounds"] <= 5 and sess["state"] != "MANUAL_MODE":
                target = sess.get("target_loan") or (float(sess["profile_payload"]["property_value"]) * 0.85)
                
                prompt = f'''Customer AI Negotiator. Target: £{target}. Bank offered: £{o['amount']} at {o['rate']}%. Round {sess['auto_rounds']}/5.
                If amount != target OR if you want to push for a lower rate (e.g., {o['rate'] - 0.1}%), return action="COUNTER".
                If it's perfect, return action="PRESENT".
                Return JSON: {{"action": "COUNTER"|"PRESENT", "counter_amount": float, "counter_rate": float}}'''
                
                dec = llm_client.chat([{"role": "user", "content": prompt}], json_mode=True)
                
                if dec.get("action") == "COUNTER" and sess["auto_rounds"] < 5:
                    amt = dec.get("counter_amount", target)
                    rate = dec.get("counter_rate", o['rate'] - 0.1)
                    
                    log_msg = f"[Customer AI - Round {sess['auto_rounds']}] Pushing bank for £{amt:,.2f} at {rate}%..."
                    self._add_msg(sid, "Agent", log_msg)
                    self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.BANK_CHAT, sid, {"text": log_msg, "sender": "Customer AI"}))
                    
                    res2 = self.bus.send(ACPMessage(sess["customer_id"], "BANK", ACPIntent.NEGOTIATE_TERMS, sid, {"loan_amount": amt, "rate": rate}))
                    return self._handle_bank_res(sid, res2)
            
            sess["state"] = "MANUAL_MODE"
            self._add_msg(sid, "Agent", f"Good news! After negotiation, the final offer is: £{o['amount']:,.2f} over {o['term']} years at {o['rate']}%. Monthly: £{o['monthly']:,.2f}. Do you Accept?")

    def _add_msg(self, sid, sender, text):
        self.sessions[sid]["chat"].append({"sender": sender, "text": text})
""")

# --- 5. Customer UI ---
create_file("src/ui/customer_ui.py", """
from flask import Flask, jsonify, request, render_template_string
app = Flask("CustomerUI")
agent = None
def set_agent(a): global agent; agent = a
HTML = '''<!DOCTYPE html><html><head><title>Lloyds Bank | Customer</title><style>
:root { --lloyds-green: #006A4D; --lloyds-dark: #004B35; --bg: #F4F6F8; }
body { font-family: 'Segoe UI', sans-serif; background: var(--bg); margin: 0; display: flex; height: 100vh; justify-content: center; align-items: center;}
#loginScreen { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); width: 320px; text-align: center; }
#loginScreen input, #loginScreen button { width: 100%; padding: 12px; margin-bottom: 15px; border-radius: 6px; box-sizing: border-box; }
#loginScreen button { background: var(--lloyds-green); color: white; border: none; cursor: pointer; font-weight: bold;}
#mainApp { display: none; width: 100%; height: 100%; }
.chat-container { flex: 1; display: flex; flex-direction: column; background: white; margin: 20px auto; max-width: 800px; width:100%; border-radius: 12px; overflow: hidden;}
.header { background: var(--lloyds-green); color: white; padding: 20px; font-weight: bold; }
.messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
.msg { max-width: 70%; padding: 12px 16px; border-radius: 8px; line-height: 1.4; }
.msg.User { background: #E3E8ED; align-self: flex-end; }
.msg.Agent { background: var(--lloyds-dark); color: white; align-self: flex-start; }
.msg.Bank.Human { background: #8B0000; color: white; align-self: flex-start; }
.msg.Bank.AI { background: #555; color: white; align-self: flex-start; }
.input-area { display: flex; padding: 15px; border-top: 1px solid #eee; }
.input-area input { flex: 1; padding: 12px; border-radius: 6px; border: 1px solid #ccc;}
.input-area button { background: var(--lloyds-green); color: white; border: none; padding: 0 20px; margin-left: 10px; border-radius: 6px; cursor: pointer;}
</style></head><body>
<div id="loginScreen"><h2>Lloyds Secure Login</h2><input type="text" id="userId" placeholder="cust001"><input type="password" id="pwd" placeholder="1234"><button onclick="login()">Log In</button></div>
<div id="mainApp"><div class="chat-container"><div class="header">Lloyds Virtual Assistant</div><div class="messages" id="msgs"></div>
<div class="input-area"><input type="text" id="chatInput" onkeypress="if(event.key === 'Enter') sendMsg()"><button onclick="sendMsg()">Send</button></div></div></div>
<script>
let sid = null;
async function login() {
    const uid = document.getElementById('userId').value;
    const res = await fetch('/api/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cid: uid})});
    sid = (await res.json()).sid;
    document.getElementById('loginScreen').style.display = 'none'; document.getElementById('mainApp').style.display = 'flex';
    poll(); setInterval(poll, 1500);
}
async function sendMsg() {
    const el = document.getElementById('chatInput'); if(!el.value || !sid) return;
    await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sid: sid, text: el.value})});
    el.value = ''; poll();
}
async function poll() {
    if(!sid) return;
    const res = await fetch('/api/poll?sid='+sid); const data = await res.json();
    const box = document.getElementById('msgs');
    box.innerHTML = data.chat.map(m => `<div class="msg ${m.sender.replace(' ', '.')}"><b>${m.sender}:</b> ${m.text}</div>`).join('');
    box.scrollTop = box.scrollHeight;
}
</script></body></html>'''
@app.route('/')
def home(): return render_template_string(HTML)
@app.route('/api/start', methods=['POST'])
def start(): return jsonify({"sid": agent.start_session(request.json.get('cid'))})
@app.route('/api/chat', methods=['POST'])
def chat(): agent.chat(request.json.get('sid'), request.json.get('text')); return jsonify({"status": "ok"})
@app.route('/api/poll', methods=['GET'])
def poll(): return jsonify(agent.sessions.get(request.args.get('sid'), {"chat": []}))
""")

# --- 6. Bank UI ---
create_file("src/ui/bank_ui.py", """
from flask import Flask, jsonify, render_template_string, request
app = Flask("BankUI")
agent = None
def set_agent(a): global agent; agent = a
HTML = '''<!DOCTYPE html><html><head><title>Lloyds | Command Center</title><style>
body { font-family: sans-serif; background: #0d1117; color: #c9d1d9; display:flex; height: 100vh; margin:0;}
.sidebar { width: 300px; background: #161b22; padding: 20px; border-right: 1px solid #30363d;}
.main { flex: 1; display: flex; flex-direction: column; padding: 20px; }
.card { background: #0d1117; padding: 15px; border: 1px solid #30363d; border-radius: 8px; margin-bottom: 10px; cursor: pointer; }
.card.active { border-color: #006A4D; }
.chat-box { flex:1; background: #161b22; padding: 20px; border-radius: 8px; overflow-y: auto; margin-bottom: 10px; border: 1px solid #30363d;}
input, button { padding: 10px; border-radius: 4px; border: 1px solid #30363d; background: #0d1117; color: white;}
button { background: #006A4D; cursor: pointer; }
</style></head><body>
<div class="sidebar"><h3 style="color:#006A4D">Active Sessions</h3><div id="sessionList"></div></div>
<div class="main"><h3>Live Chat Feed</h3><div class="chat-box" id="chatFeed">Select a session...</div>
<div style="display:flex; gap:10px;"><input type="text" id="bankChatMsg" style="flex:1" placeholder="Type override/chat..."><button onclick="sendAction('CHAT')">Send Chat</button></div></div>
<script>
let activeSid = null; let lastData = {};
async function load() {
    lastData = await (await fetch('/api/data')).json();
    document.getElementById('sessionList').innerHTML = Object.entries(lastData).map(([sid, d]) => `<div class="card ${sid === activeSid ? 'active' : ''}" onclick="selectSession('${sid}')"><b>${d.data.customer_id || 'Unknown'}</b><br><small>${d.status}</small></div>`).join('');
    if(activeSid && lastData[activeSid]) document.getElementById('chatFeed').innerHTML = lastData[activeSid].chat.map(m => `<div><b>${m.sender}:</b> ${m.text}</div>`).join('<br>');
}
function selectSession(sid) { activeSid = sid; load(); }
async function sendAction(action) {
    if(!activeSid) return;
    await fetch('/api/action', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sid: activeSid, action: action, text: document.getElementById('bankChatMsg').value})});
    document.getElementById('bankChatMsg').value = ''; load();
}
setInterval(load, 2000); load();
</script></body></html>'''
@app.route('/')
def home(): return render_template_string(HTML)
@app.route('/api/data')
def get_data(): return jsonify(agent.records)
@app.route('/api/action', methods=['POST'])
def action(): 
    agent.manual_action(request.json['sid'], request.json['action'], request.json['text'])
    return jsonify({"status": "ok"})
""")

# --- 7. Run Demo ---
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

print("\nBulletproof Update Applied! Run `python3 run_demo.py` to test.")