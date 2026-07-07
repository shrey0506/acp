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
