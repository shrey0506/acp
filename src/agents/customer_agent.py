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
