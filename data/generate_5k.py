import pandas as pd
import random
import os

def generate_5k_customers(filepath="data/customers_5k.csv"):
    if os.path.exists(filepath): return pd.read_csv(filepath)
    print("Generating 5000 customer records...")
    data = []
    for i in range(1, 5001):
        tier = random.random()
        if tier > 0.8: inc, cs, prop = random.randint(100000, 300000), random.randint(750, 850), random.randint(500000, 1500000)
        elif tier > 0.2: inc, cs, prop = random.randint(40000, 99000), random.randint(650, 749), random.randint(200000, 499000)
        else: inc, cs, prop = random.randint(25000, 45000), random.randint(450, 649), random.randint(100000, 250000)
        data.append({"customer_id": f"CUST{i:05d}", "annual_income": inc, "credit_score": cs, "property_value": prop})
    df = pd.DataFrame(data)
    df.to_csv(filepath, index=False)
    return df
