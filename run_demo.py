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
    
    print("\n=== SYSTEM ONLINE ===")
    print("CUSTOMER LOGIN: http://localhost:5001  (Use cust001, password 1234)")
    print("BANK DASHBOARD: http://localhost:5002")
    
    customer_app.run(port=5001, debug=False, use_reloader=False)
