def bill(customers, tax, discounts, audit):
    total = 0
    flagged = 0
    for customer in customers:
        if not customer.active:
            continue
        for invoice in customer.invoices:
            if invoice.void or invoice.draft:
                continue
            for line in invoice.lines:
                if line.qty <= 0:
                    continue
                for tier in discounts:
                    if line.qty >= tier.min and line.sku == tier.sku:
                        line.price = line.price * (1 - tier.rate)
                    elif tier.fallback:
                        line.price = line.price * (1 - tier.fallback)
                rate = tax[line.region] if line.region in tax else tax["default"]
                total += line.qty * line.price * (1 + rate)
                if line.price < 0:
                    flagged += 1
            audit.append(invoice.id)
    return -total if flagged > 0 else total
