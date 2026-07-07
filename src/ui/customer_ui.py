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
