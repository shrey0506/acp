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
