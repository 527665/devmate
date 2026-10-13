from decimal import Decimal
from typing import List, Dict, Optional, Union


def calc_subtotal(items: List[Dict[str, Union[Decimal, int]]]) -> Decimal:
    """
    Calculate the subtotal from a list of items.
    
    Args:
        items: A list of dictionaries, each containing 'price' (Decimal) and 'qty' (int)
        
    Returns:
        Decimal: The calculated subtotal
        
    Raises:
        ValueError: If items list is empty or contains invalid data
    """
    if not items:
        return Decimal('0')
    
    subtotal = Decimal('0')
    for item in items:
        if 'price' not in item or 'qty' not in item:
            raise ValueError("Each item must contain 'price' and 'qty' keys")
        
        price = item['price']
        qty = item['qty']
        
        if not isinstance(price, Decimal):
            raise ValueError("Price must be a Decimal")
        if not isinstance(qty, int) or qty < 0:
            raise ValueError("Quantity must be a non-negative integer")
            
        subtotal += price * qty
        
    return subtotal


def apply_coupon(subtotal: Decimal, coupon: Optional[Dict[str, Union[str, Decimal, int]]]) -> Decimal:
    """
    Apply a coupon to the subtotal.
    
    Supports two types of coupons:
    - 'fixed_amount': Fixed amount discount (满减)
    - 'percentage': Percentage discount (打折)
    
    Args:
        subtotal: The original subtotal (Decimal)
        coupon: A dictionary containing coupon information with 'type' key
                For 'fixed_amount': {'type': 'fixed_amount', 'amount': Decimal}
                For 'percentage': {'type': 'percentage', 'rate': Decimal} where rate is between 0 and 1
                
    Returns:
        Decimal: The discounted amount
        
    Raises:
        ValueError: If coupon type is invalid or coupon parameters are invalid
    """
    if coupon is None:
        return subtotal
        
    if 'type' not in coupon:
        raise ValueError("Coupon must contain 'type' key")
        
    coupon_type = coupon['type']
    
    if coupon_type == 'fixed_amount':
        if 'amount' not in coupon:
            raise ValueError("Fixed amount coupon must contain 'amount' key")
        amount = coupon['amount']
        if not isinstance(amount, Decimal):
            raise ValueError("Coupon amount must be a Decimal")
        if amount < 0:
            raise ValueError("Coupon amount cannot be negative")
        return max(Decimal('0'), subtotal - amount)
        
    elif coupon_type == 'percentage':
        if 'rate' not in coupon:
            raise ValueError("Percentage coupon must contain 'rate' key")
        rate = coupon['rate']
        if not isinstance(rate, Decimal):
            raise ValueError("Coupon rate must be a Decimal")
        if rate < 0 or rate > 1:
            raise ValueError("Coupon rate must be between 0 and 1")
        return subtotal * (1 - rate)
        
    else:
        raise ValueError(f"Unsupported coupon type: {coupon_type}")


def calc_final_total(
    items: List[Dict[str, Union[Decimal, int]]], 
    coupon: Optional[Dict[str, Union[str, Decimal, int]]], 
    vip_level: int = 0
) -> Decimal:
    """
    Calculate the final total amount after applying coupon and VIP discount.
    
    VIP levels and their corresponding discounts:
    - Level 0: No discount (1.0)
    - Level 1: 5% discount (0.95)
    - Level 2: 10% discount (0.90)
    - Level 3: 15% discount (0.85)
    - Level 4+: 20% discount (0.80)
    
    Args:
        items: A list of dictionaries, each containing 'price' (Decimal) and 'qty' (int)
        coupon: A dictionary containing coupon information (see apply_coupon for details)
        vip_level: VIP level (non-negative integer)
        
    Returns:
        Decimal: The final total amount
        
    Raises:
        ValueError: If inputs are invalid
    """
    if vip_level < 0:
        raise ValueError("VIP level cannot be negative")
        
    # Get VIP discount rate
    if vip_level == 0:
        vip_discount = Decimal('1.0')
    elif vip_level == 1:
        vip_discount = Decimal('0.95')
    elif vip_level == 2:
        vip_discount = Decimal('0.90')
    elif vip_level == 3:
        vip_discount = Decimal('0.85')
    else:  # vip_level >= 4
        vip_discount = Decimal('0.80')
    
    # Calculate subtotal
    subtotal = calc_subtotal(items)
    
    # Apply coupon
    discounted_amount = apply_coupon(subtotal, coupon)
    
    # Apply VIP discount
    final_total = discounted_amount * vip_discount
    
    return final_total