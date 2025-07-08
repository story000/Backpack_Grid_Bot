import json
import websocket
import threading
import time
from typing import Dict, Callable
from backpack.bpx import BpxClient

class BackpackOrderWebSocket:
    """WebSocket client for real-time order updates via Backpack Exchange"""
    
    def __init__(self, api_key: str, api_secret: str, symbol: str = None):
        self.client = BpxClient()
        self.client.init(api_key, api_secret)
        self.symbol = symbol
        self.ws = None
        self.is_connected = False
        self.callbacks = {
            'orderAccepted': [],
            'orderCancelled': [],
            'orderExpired': [],
            'orderFill': [],
            'orderModified': [],
            'triggerPlaced': [],
            'triggerFailed': []
        }
        
    def add_callback(self, event_type: str, callback: Callable):
        """Add callback for specific event types"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
            
    def _emit_event(self, event_type: str, data: dict):
        """Emit event to all registered callbacks"""
        for callback in self.callbacks.get(event_type, []):
            try:
                callback(data)
            except Exception as e:
                print(f"Callback error: {e}")
    
    def on_open(self, ws):
        print("🟢 Order WebSocket connected")
        self.is_connected = True
        
        # Get signature for subscription (instruction changed to 'subscribe')
        signature_data = self.client.ws_sign('subscribe', {})
        
        # Subscribe to order updates - either all markets or specific symbol
        if self.symbol:
            stream_name = f"account.orderUpdate.{self.symbol}"
        else:
            stream_name = "account.orderUpdate"
        
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [stream_name],
            "signature": signature_data
        }

        
        print(f"📡 Subscribing to: {stream_name}")
        ws.send(json.dumps(subscribe_msg))
        
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            
            # Handle subscription confirmation
            if 'result' in data:
                if data['result'] is None:
                    print("✅ Successfully subscribed to order updates")
                else:
                    print(f"❌ Subscription failed: {data}")
                return
            
            # Handle order update messages
            if 'stream' in data and 'account.orderUpdate' in data['stream']:
                order_data = data.get('data', {})
                
                # Extract key order information
                order_info = {
                    'event_type': order_data.get('e'),          # Event type, e.g., orderAccepted, orderFill
                    'event_time': order_data.get('E'),          # Event timestamp (microseconds)
                    'symbol': order_data.get('s'),              # Trading symbol, e.g., SOL_USD
                    'order_id': order_data.get('i'),            # Order ID
                    'client_order_id': order_data.get('c'),     # Client-provided order ID
                    'side': order_data.get('S'),                # Side: Bid / Ask
                    'order_type': order_data.get('o'),          # Order type: LIMIT / MARKET / STOP, etc.
                    'time_in_force': order_data.get('f'),       # Time in force: GTC / IOC / FOK
                    'price': order_data.get('p'),               # Order price
                    'trigger_price': order_data.get('P'),       # Trigger price (if stop/trigger order)
                    'trigger_by': order_data.get('B'),          # Trigger condition: LastPrice / MarkPrice / IndexPrice
                    'take_profit_trigger_price': order_data.get('a'),  # Take profit trigger price
                    'stop_loss_trigger_price': order_data.get('b'),    # Stop loss trigger price
                    'take_profit_trigger_by': order_data.get('d'),     # Take profit trigger condition
                    'stop_loss_trigger_by': order_data.get('g'),       # Stop loss trigger condition
                    'trigger_quantity': order_data.get('Y'),           # Trigger quantity

                    'quantity': order_data.get('q'),            # Order base quantity
                    'quantity_quote': order_data.get('Q'),      # Order quote quantity (if reverse market order)

                    'filled_quantity': order_data.get('z'),     # Cumulative filled quantity
                    'filled_quantity_quote': order_data.get('Z'), # Cumulative filled quantity in quote
                    'last_fill_quantity': order_data.get('l'),  # Last fill quantity (this fill)
                    'last_fill_price': order_data.get('L'),     # Last fill price (this fill)
                    'trade_id': order_data.get('t'),            # Trade ID (if order fill)
                    'is_maker': order_data.get('m'),            # Whether this fill was maker side

                    'status': order_data.get('X'),              # Order status/state: Accepted / Filled / Cancelled / Expired / etc.
                    'expiry_reason': order_data.get('R'),       # If expired, reason for expiry (e.g. PRICE_BAND)

                    'fee': order_data.get('n'),                 # Fee amount for this fill
                    'fee_symbol': order_data.get('N'),          # Fee currency symbol

                    'self_trade_prevention': order_data.get('V'), # Self trade prevention strategy: RejectTaker / CancelMaker / etc.

                    'engine_timestamp': order_data.get('T'),    # Engine timestamp in microseconds
                    'origin': order_data.get('O'),              # Origin of update: USER / LIQUIDATION_AUTOCLOSE / ADL_AUTOCLOSE / etc.
                    'related_order_id': order_data.get('I'),    # Related order ID if applicable
                }

                # Get the event type from the order data
                event_type = order_info.get('event_type')
                
                if event_type:
                    # Emit specific event based on type
                    if event_type == 'orderAccepted':
                        # Don't print here, let the callback handle logging to avoid duplicates
                        self._emit_event('orderAccepted', order_info)
                        
                    elif event_type == 'orderFill':
                        # Let the grid bot handle fill logging to avoid duplicates
                        self._emit_event('orderFill', order_info)
                        
                    elif event_type == 'orderCancelled':
                        self._emit_event('orderCancelled', order_info)
                        
                    elif event_type == 'orderExpired':
                        self._emit_event('orderExpired', order_info)
                        
                    elif event_type == 'orderModified':
                        self._emit_event('orderModified', order_info)
                        
                    elif event_type == 'triggerPlaced':
                        self._emit_event('triggerPlaced', order_info)
                        
                    elif event_type == 'triggerFailed':
                        self._emit_event('triggerFailed', order_info)
                        
                    else:
                        print(f"❓ Unknown event type: {event_type}")
                        print(f"Order info: {order_info}")
                else:
                    print(f"❓ No event type found in order data: {order_info}")
                
        except Exception as e:
            print(f"Message parsing error: {e}")
            print(f"Raw message: {message}")
    
    def on_error(self, ws, error):
        print(f"❌ WebSocket error: {error}")
        self.is_connected = False
        
    def on_close(self, ws, close_status_code, close_msg):
        print(f"🔌 WebSocket closed: {close_status_code} - {close_msg}")
        self.is_connected = False
        
    def start(self):
        """Start WebSocket connection"""        
        self.ws = websocket.WebSocketApp(
            "wss://ws.backpack.exchange",
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # Run in separate thread
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
    def stop(self):
        """Stop WebSocket connection"""
        if self.ws:
            self.ws.close()
            self.is_connected = False

# Example usage callbacks
def on_order_accepted(order_data):
    print(f"✅ Callback: Order accepted - {order_data['order_id']}")

def on_order_filled(fill_data):
    print(f"🎯 Callback: Order fill - {fill_data['order_id']} filled {fill_data['last_fill_quantity']} @ {fill_data['last_fill_price']}")

def on_order_cancelled(cancel_data):
    print(f"❌ Callback: Order cancelled - {cancel_data['order_id']}")

def on_order_expired(expire_data):
    print(f"⏰ Callback: Order expired - {expire_data['order_id']}")

# Test mode - run this file directly to test WebSocket connection
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    api_key = os.getenv("BACKPACK_API_KEY")
    api_secret = os.getenv("BACKPACK_API_SECRET")
    
    if not api_key or not api_secret:
        print("❌ Please set BACKPACK_API_KEY and BACKPACK_API_SECRET in your .env file")
        exit(1)
        
    print("🚀 Testing Backpack WebSocket Order Stream...")
    print(f"API Key: {api_key[:8]}...")
    
    # Create WebSocket client for testing
    order_ws = BackpackOrderWebSocket(api_key, api_secret, "SOL_USDC_PERP")
    
    # Register callbacks for different event types
    order_ws.add_callback('orderAccepted', on_order_accepted)
    order_ws.add_callback('orderFill', on_order_filled)
    order_ws.add_callback('orderCancelled', on_order_cancelled)
    order_ws.add_callback('orderExpired', on_order_expired)
    
    try:
        order_ws.start()
        print("WebSocket started. Press Ctrl+C to stop...")
        print("Place/cancel orders on Backpack to see events...")
        
        # Keep alive
        import time
        while True:
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n🛑 Stopping WebSocket...")
        order_ws.stop()
        print("WebSocket stopped.")