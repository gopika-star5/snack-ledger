from __future__ import annotations

from decimal import Decimal
import urllib.parse


def format_inr(paise: int | float | Decimal | None) -> str:
    """
    Format integer paise as Indian Rupees string, e.g. 787500 -> '₹7,875.00'
    """
    if paise is None:
        return "₹0.00"
    
    rupees = Decimal(str(paise)) / Decimal(100)
    is_neg = rupees < 0
    rupees = abs(rupees)
    
    parts = f"{rupees:.2f}".split(".")
    int_part = parts[0]
    dec_part = parts[1]
    
    if len(int_part) > 3:
        last3 = int_part[-3:]
        remaining = int_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        formatted_int = ",".join(groups) + "," + last3
    else:
        formatted_int = int_part
        
    return f"{'-' if is_neg else ''}₹{formatted_int}.{dec_part}"


def _num_to_words(n: int) -> str:
    units = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen"
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    if n == 0:
        return ""
    elif n < 20:
        return units[n]
    elif n < 100:
        unit_str = units[n % 10]
        return f"{tens[n // 10]}-{unit_str}" if unit_str else tens[n // 10]
    elif n < 1000:
        rem = n % 100
        return units[n // 100] + " Hundred" + (f" and {_num_to_words(rem)}" if rem else "")
    elif n < 100000:
        rem = n % 1000
        return _num_to_words(n // 1000) + " Thousand" + (f" {_num_to_words(rem)}" if rem else "")
    elif n < 10000000:
        rem = n % 100000
        return _num_to_words(n // 100000) + " Lakh" + (f" {_num_to_words(rem)}" if rem else "")
    else:
        rem = n % 10000000
        return _num_to_words(n // 10000000) + " Crore" + (f" {_num_to_words(rem)}" if rem else "")


def amount_to_words_inr(paise: int | float | Decimal | None) -> str:
    """
    Convert amount in paise to words in Indian Rupees.
    E.g. 787500 -> 'Rupees Seven Thousand Eight Hundred and Seventy-Five Only'
    """
    if not paise or int(paise) <= 0:
        return "Rupees Zero Only"
    
    total_paise = int(paise)
    rupees = total_paise // 100
    rem_paise = total_paise % 100
    
    words = []
    if rupees > 0:
        words.append(f"Rupees {_num_to_words(rupees)}")
    
    if rem_paise > 0:
        paise_str = _num_to_words(rem_paise)
        if words:
            words.append(f"and {paise_str} Paise")
        else:
            words.append(f"{paise_str} Paise")
            
    return " ".join(words) + " Only"


def get_whatsapp_invoice_url(invoice) -> str:
    """
    Generate wa.me link with invoice text.
    """
    lines = [
        f"Tax Invoice: {invoice.number}",
        f"Date: {invoice.date}",
        f"Customer: {invoice.customer.name}",
    ]
    if invoice.customer.gstin:
        lines.append(f"GSTIN: {invoice.customer.gstin}")
    lines.extend([
        f"Place of Supply: {invoice.place_of_supply}",
        f"Total Amount: {format_inr(invoice.total_paise)}",
        f"Status: {invoice.get_status_display()}",
    ])
    text = "\n".join(lines)
    quoted = urllib.parse.quote(text)
    
    phone = "".join(ch for ch in (invoice.customer.phone or "") if ch.isdigit())
    if phone:
        if len(phone) == 10:
            phone = f"91{phone}"
        return f"https://wa.me/{phone}?text={quoted}"
    return f"https://wa.me/?text={quoted}"
