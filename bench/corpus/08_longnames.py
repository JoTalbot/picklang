def process(customer_identifier, payment_amount, warehouse, payments, logistics):
    comp = []
    try:
        reservation_result = warehouse.reserve(customer_identifier)
        comp.append(lambda: warehouse.release(customer_identifier))
        payment_result = payments.charge(customer_identifier, payment_amount)
        comp.append(lambda: payments.refund(payment_result["transaction"]))
        if not payment_result["approved"]: raise ValueError("платёж не одобрен")
        delivery_result = logistics.book(customer_identifier)
        comp.append(lambda: logistics.cancel(delivery_result["tracking"]))
        return {"transaction": payment_result["transaction"], "tracking": delivery_result["tracking"]}
    except Exception:
        for undo in reversed(comp): undo()
        raise
