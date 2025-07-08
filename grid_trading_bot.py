import os
import time
import logging
import json
from typing import Dict, List, Optional
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from threading import Thread, Lock
import signal
import sys

from dotenv import load_dotenv
from backpack.bpx import BpxClient
from backpack.bpx_pub import Markets, Ticker, Depth
from grid_websocket_orders import BackpackOrderWebSocket


@dataclass
class GridLevel:
    price: Decimal
    quantity: Decimal
    order_id: Optional[str] = None
    is_buy: bool = True


@dataclass
class GridConfig:
    symbol: str = "SOL_USDC_PERP"
    base_sol_amount: Decimal = Decimal("5.0")  # SOL amount for grid
    grid_count: int = 10
    grid_spread: Decimal = Decimal("0.02")  # 2% spread between grids
    buy_grid_ratio: Decimal = Decimal("0.5")  # Ratio of grids below current price
    sell_grid_ratio: Decimal = Decimal("0.5")  # Ratio of grids above current price
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    description: str = "Balanced grid strategy"
    api_key_env: str = "BACKPACK_API_KEY"
    api_secret_env: str = "BACKPACK_API_SECRET"
    
    
class GridTradingBot:
    def __init__(self, config: GridConfig):
        self.config = config
        self.client = BpxClient()
        self.grid_levels: List[GridLevel] = []
        self.current_price = Decimal("0")
        self.is_running = False
        self.lock = Lock()
        
        # Setup logging with symbol-specific log file
        log_filename = f'grid_bot_{config.symbol.replace("_", "").lower()}.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(f"{__name__}_{config.symbol}")
        
        # Initialize WebSocket placeholder
        self.order_ws = None
        
        # Initialize API credentials from environment
        self._init_credentials()
        
        # Market info
        self.symbol_info = None
        self.tick_size = Decimal("0.01")
        self.step_size = Decimal("0.001")
        
    def _init_credentials(self):
        """Initialize API credentials from environment variables"""
        load_dotenv()
        api_key = os.getenv("BACKPACK_API_KEY")
        api_secret = os.getenv("BACKPACK_API_SECRET")
        
        if not api_key or not api_secret:
            raise ValueError(f"Missing BACKPACK_API_KEY or BACKPACK_API_SECRET environment variables")
            
        self.client.init(api_key, api_secret)
        
        # Initialize WebSocket for real-time order monitoring
        try:
            self.order_ws = BackpackOrderWebSocket(api_key, api_secret, self.config.symbol)
            self.order_ws.add_callback('orderFill', self._on_order_filled)
            self.order_ws.add_callback('orderCancelled', self._on_order_cancelled)
            self.order_ws.add_callback('orderExpired', self._on_order_expired)
            self.order_ws.add_callback('orderAccepted', self._on_order_accepted)
            
            self.logger.info(f"API credentials and WebSocket initialized for {self.config.symbol} using {self.config.api_key_env}")
        except Exception as e:
            self.logger.error(f"Failed to initialize WebSocket: {e}")
            self.order_ws = None
            raise
        
    def _get_symbol_info(self):
        """Get trading symbol information"""
        try:
            markets = Markets()
            for market in markets:
                if market['symbol'] == self.config.symbol:
                    self.symbol_info = market
                    # Extract tick size and step size from nested filters
                    filters = market.get('filters', {})
                    price_filter = filters.get('price', {})
                    quantity_filter = filters.get('quantity', {})
                    
                    self.tick_size = Decimal(str(price_filter.get('tickSize', '0.01')))
                    self.step_size = Decimal(str(quantity_filter.get('stepSize', '0.001')))
                    
                    self.logger.info(f"Symbol info loaded for {self.config.symbol}")
                    self.logger.info(f"Tick size: {self.tick_size}, Step size: {self.step_size}")
                    return
            raise ValueError(f"Symbol {self.config.symbol} not found")
        except Exception as e:
            self.logger.error(f"Failed to get symbol info: {e}")
            raise
            
    def _get_current_price(self) -> Decimal:
        """Get current market price"""
        try:
            ticker = Ticker(self.config.symbol)
            price = Decimal(str(ticker['lastPrice']))
            self.current_price = price
            return price
        except Exception as e:
            self.logger.error(f"Failed to get current price: {e}")
            return self.current_price
            
    def _round_price(self, price: Decimal) -> Decimal:
        """Round price to tick size"""
        return (price / self.tick_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * self.tick_size
        
    def _round_quantity(self, quantity: Decimal) -> Decimal:
        """Round quantity to step size"""
        return (quantity / self.step_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * self.step_size
        
    def _calculate_grid_levels(self):
        """Calculate grid trading levels based on SOL amount and bias"""
        current_price = self._get_current_price()
        if current_price == 0:
            raise ValueError("Unable to get current price")
            
        # Calculate buy and sell grid counts based on ratios
        buy_grids = int(self.config.grid_count * self.config.buy_grid_ratio)
        sell_grids = self.config.grid_count - buy_grids
        
        # Set price range if not specified
        if not self.config.min_price:
            self.config.min_price = current_price * (1 - self.config.grid_spread * buy_grids)
        if not self.config.max_price:
            self.config.max_price = current_price * (1 + self.config.grid_spread * sell_grids)
            
        self.grid_levels = []
        
        # Calculate SOL quantity per grid level
        sol_per_grid = self.config.base_sol_amount / self.config.grid_count
        
        # Generate BUY orders below current price
        if buy_grids > 0:
            buy_step = (current_price - self.config.min_price) / buy_grids
            for i in range(buy_grids):
                price = self.config.min_price + buy_step * i
                price = self._round_price(price)
                quantity = self._round_quantity(sol_per_grid)
                
                grid_level = GridLevel(
                    price=price,
                    quantity=quantity,
                    is_buy=True
                )
                self.grid_levels.append(grid_level)
        
        # Generate SELL orders above current price
        if sell_grids > 0:
            sell_step = (self.config.max_price - current_price) / sell_grids
            for i in range(1, sell_grids + 1):
                price = current_price + sell_step * i
                price = self._round_price(price)
                quantity = self._round_quantity(sol_per_grid)
                
                grid_level = GridLevel(
                    price=price,
                    quantity=quantity,
                    is_buy=False
                )
                self.grid_levels.append(grid_level)
        
        # Sort by price
        self.grid_levels.sort(key=lambda x: x.price)
            
        self.logger.info(f"Generated {len(self.grid_levels)} grid levels ({buy_grids} BUY, {sell_grids} SELL)")
        for level in self.grid_levels:
            self.logger.info(f"Grid: {level.price} | {level.quantity} | {'BUY' if level.is_buy else 'SELL'}")
            
    def _place_grid_orders(self):
        """Place all grid orders using batch execution with max 50 orders per batch"""
        try:
            # Prepare all orders
            orders = []
            for level in self.grid_levels:
                side = "Bid" if level.is_buy else "Ask"
                order = {
                    "symbol": self.config.symbol,
                    "side": side,
                    "orderType": "Limit",
                    "postOnly": True,
                    "quantity": str(level.quantity),
                    "price": str(level.price)
                }
                orders.append(order)
            
            total_orders = len(orders)
            batch_size = 50
            for start in range(0, total_orders, batch_size):
                end = min(start + batch_size, total_orders)
                batch_orders = orders[start:end]

                # Execute batch orders
                response = self.client.ExeOrders(batch_orders)

                if isinstance(response, list):
                    # Handle successful batch response
                    for i, order_response in enumerate(response):
                        grid_index = start + i
                        if 'id' in order_response and grid_index < len(self.grid_levels):
                            self.grid_levels[grid_index].order_id = order_response['id']
                            side = "BUY" if self.grid_levels[grid_index].is_buy else "SELL"
                            self.logger.info(f"📝 Order placed: {side} {self.grid_levels[grid_index].quantity} @ {self.grid_levels[grid_index].price} | ID: {order_response['id']}")
                        elif grid_index < len(self.grid_levels):
                            self.logger.error(f"Failed to place order at {self.grid_levels[grid_index].price}: {order_response}")
                    
                    self.logger.info(f"✅ Batch order execution completed: {len(batch_orders)} orders submitted")
                else:
                    self.logger.error(f"❌ Batch order execution failed: {response}")
                    # If one batch fails, fallback to individual placement for that batch
                    self.logger.info("🔄 Falling back to individual order placement for this batch...")
                    self._place_individual_orders(batch_orders, start)

        except Exception as e:
            self.logger.error(f"⚠️ Error placing batch orders: {e}")
            self.logger.info("🔄 Falling back to individual order placement for all orders...")
            self._place_grid_orders_individual()
    
    def _place_grid_orders_individual(self):
        """Fallback method to place orders individually"""
        for level in self.grid_levels:
            try:
                side = "Bid" if level.is_buy else "Ask"
                response = self.client.ExeOrder(
                    symbol=self.config.symbol,
                    side=side,
                    orderType="Limit",
                    timeInForce="",  # Post-only
                    quantity=str(level.quantity),
                    price=str(level.price)
                )
                if 'id' in response:
                    level.order_id = response['id']
                    self.logger.info(f"Order placed: {side} {level.quantity} @ {level.price} | ID: {level.order_id}")
                else:
                    self.logger.error(f"Failed to place order: {response}")
                    
                time.sleep(0.1)  # Rate limiting
                
            except Exception as e:
                self.logger.error(f"Error placing order at {level.price}: {e}")
                
    def _on_order_filled(self, fill_data: Dict):
        """Handle real-time order fills via WebSocket"""
        order_id = fill_data['order_id']
        
        with self.lock:
            # Find the grid level that was filled
            for level in self.grid_levels:
                if level.order_id == order_id:
                    self.logger.info(f"🎯 Grid order filled: {order_id} - {fill_data['side']} {fill_data['last_fill_quantity']} @ {fill_data['last_fill_price']}")
                    self.logger.info(f"   💰 Fee: {fill_data.get('fee', 'N/A')} {fill_data.get('fee_symbol', '')}")
                    self.logger.info(f"   📊 Total filled: {fill_data['filled_quantity']} / {fill_data['quantity']}")
                    
                    # Check if order is completely filled
                    if Decimal(str(fill_data['filled_quantity'])) >= Decimal(str(fill_data['quantity'])):
                        self.logger.info(f"Order completely filled, triggering rebalance...")
                        self._rebalance_grid(level)
                    else:
                        self.logger.info(f"Partial fill, waiting for complete fill...")
                    break
            else:
                self.logger.warning(f"Received fill for unknown order: {order_id}")
                
    def _on_order_cancelled(self, cancel_data: Dict):
        """Handle order cancellations via WebSocket"""
        order_id = cancel_data['order_id']
        
        with self.lock:
            for level in self.grid_levels:
                if level.order_id == order_id:
                    self.logger.warning(f"❌ Grid order cancelled: {order_id} at {level.price}")
                    level.order_id = None  # Clear order ID so it can be re-placed
                    break
                    
    def _on_order_expired(self, expire_data: Dict):
        """Handle order expirations via WebSocket"""
        order_id = expire_data['order_id']
        reason = expire_data.get('expiry_reason', 'Unknown')
        
        with self.lock:
            for level in self.grid_levels:
                if level.order_id == order_id:
                    self.logger.warning(f"⏰ Grid order expired: {order_id} at {level.price} - Reason: {reason}")
                    level.order_id = None  # Clear order ID so it can be re-placed
                    break
                    
    def _on_order_accepted(self, accept_data: Dict):
        """Handle order acceptances via WebSocket"""
        order_id = accept_data['order_id']
        
        # Find the grid level and confirm order ID
        with self.lock:
            for level in self.grid_levels:
                if level.order_id == order_id:
                    # Only log if this is a new acceptance (avoid duplicate logs)
                    self.logger.debug(f"✅ Grid order accepted: {order_id} - {accept_data['side']} {accept_data['quantity']} @ {accept_data['price']}")
                    break
    def _place_order_with_retry(self, side: str, quantity: Decimal, target_price: Decimal, max_retries: int = 5) -> dict:
        """
        Place order with retry mechanism until success
        First try target price, then fall back to safe prices from orderbook
        """
        
        for attempt in range(max_retries):
            try:
                # First attempt: use target price
                if attempt == 0:
                    price_to_use = target_price
                    self.logger.info(f"Attempt {attempt + 1}: Using target price {price_to_use}")
                else:
                    # Subsequent attempts: use safe price from orderbook
                    is_buy = (side == "Bid")
                    price_to_use = self._get_safe_price_from_orderbook(is_buy)
                    self.logger.info(f"Attempt {attempt + 1}: Using safe price {price_to_use}")
                
                response = self.client.ExeOrder(
                    symbol=self.config.symbol,
                    side=side,
                    orderType="Limit",
                    timeInForce="",
                    quantity=str(quantity),
                    price=str(price_to_use)
                )
                
                # Check if order was successful
                if 'id' in response:
                    self.logger.info(f"Order successful on attempt {attempt + 1}: {side} {quantity} @ {price_to_use} | ID: {response['id']}")
                    return response
                else:
                    self.logger.warning(f"Order rejected on attempt {attempt + 1}: {response}")
                    
            except Exception as e:
                self.logger.warning(f"Order failed on attempt {attempt + 1}: {e}")
            
            # Wait before retry (except on last attempt)
            if attempt < max_retries - 1:
                time.sleep(0.5)
                self.logger.info(f"Retrying in 0.5 seconds...")
        
        # All attempts failed
        self.logger.error(f"All {max_retries} attempts failed for {side} order")
        return {}

    def _get_safe_price_from_orderbook(self, is_buy: bool) -> Decimal:
        """
        Get safe price from orderbook to avoid rejection
        Uses second best bid/ask or fallback to first level +/- tick
        """
        try:
            depth = Depth(self.config.symbol)
            
            if is_buy:
                # For buy orders: use second best bid price
                if len(depth['bids']) >= 2:
                    safe_price = Decimal(str(depth['bids'][1][0]))  # Buy second price
                    self.logger.debug(f"Using buy second price: {safe_price}")
                else:
                    # Fallback: best bid minus one tick
                    best_bid = Decimal(str(depth['bids'][0][0]))
                    safe_price = best_bid - self.tick_size
                    self.logger.debug(f"Using best bid minus tick: {safe_price}")
            else:
                # For sell orders: use second best ask price
                if len(depth['asks']) >= 2:
                    safe_price = Decimal(str(depth['asks'][1][0]))  # Sell second price
                    self.logger.debug(f"Using sell second price: {safe_price}")
                else:
                    # Fallback: best ask plus one tick
                    best_ask = Decimal(str(depth['asks'][0][0]))
                    safe_price = best_ask + self.tick_size
                    self.logger.debug(f"Using best ask plus tick: {safe_price}")
            
            return self._round_price(safe_price)
            
        except Exception as e:
            self.logger.error(f"Failed to get safe price from orderbook: {e}")
            # Final fallback: use current price with spread
            current_price = self._get_current_price()
            if is_buy:
                fallback_price = current_price * (1 - self.config.grid_spread)
            else:
                fallback_price = current_price * (1 + self.config.grid_spread)
            
            self.logger.warning(f"Using fallback price: {fallback_price}")
            return self._round_price(fallback_price)
        
    def _rebalance_grid(self, filled_level: GridLevel):
        """Rebalance grid after an order is filled"""
        try:
            current_price = filled_level.price
            
            # Place opposite order
            new_is_buy = not filled_level.is_buy
            
            if new_is_buy:
                # Place buy order below current price
                new_price = current_price * (1 - self.config.grid_spread)
            else:
                # Place sell order above current price
                new_price = current_price * (1 + self.config.grid_spread)
                
            new_price = self._round_price(new_price)
            
            # Use constant quantity for rebalance
            sol_per_grid = self.config.base_sol_amount / self.config.grid_count
            quantity = self._round_quantity(sol_per_grid)
                
            side = "Bid" if new_is_buy else "Ask"
            
            # Place order with retry until success
            response = self._place_order_with_retry(side, quantity, new_price)
            
            if 'id' in response:
                filled_level.order_id = response['id']
                actual_price = Decimal(str(response.get('price', new_price)))
                filled_level.price = actual_price
                filled_level.quantity = quantity
                filled_level.is_buy = new_is_buy
                
                self.logger.info(f"Rebalanced: {side} {quantity} @ {new_price} | ID: {filled_level.order_id}")
            else:
                self.logger.error(f"Failed to rebalance: {response}")
                
        except Exception as e:
            self.logger.error(f"Error rebalancing grid: {e}")
            
    def _cancel_all_orders(self):
        """Cancel all open orders"""
        try:
            self.client.ordersCancel(self.config.symbol)
            self.logger.info("All orders cancelled")
        except Exception as e:
            self.logger.error(f"Error cancelling orders: {e}")
            
    def _monitor_connection(self):
        """Monitor WebSocket connection and grid status"""
        while self.is_running:
            try:
                # Check WebSocket connection status
                if self.order_ws and not self.order_ws.is_connected:
                    self.logger.warning("⚠️ WebSocket disconnected, attempting to reconnect...")
                    try:
                        self.order_ws.start()
                        time.sleep(2)  # Wait for connection
                    except Exception as e:
                        self.logger.error(f"Failed to reconnect WebSocket: {e}")
                
                # Log grid status periodically
                with self.lock:
                    active_orders = len([level for level in self.grid_levels if level.order_id])
                    total_levels = len(self.grid_levels)
                    ws_status = "🟢 Connected" if (self.order_ws and self.order_ws.is_connected) else "🔴 Disconnected"
                    self.logger.info(f"Grid Status: {active_orders}/{total_levels} orders active | WebSocket: {ws_status}")
                    
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Error in connection monitoring: {e}")
                time.sleep(10)
                
    def start(self):
        """Start the grid trading bot with WebSocket monitoring"""
        try:
            self.logger.info("Starting Grid Trading Bot with WebSocket...")
            
            # Initialize market info and grid levels
            self._get_symbol_info()
            self._calculate_grid_levels()
            
            # Start WebSocket for order monitoring
            if self.order_ws:
                self.order_ws.start()
                time.sleep(2)  # Wait for WebSocket to connect
                
                if not self.order_ws.is_connected:
                    self.logger.warning("⚠️ WebSocket failed to connect, but continuing...")
            else:
                self.logger.error("❌ WebSocket not initialized, cannot start real-time monitoring")
                raise ValueError("WebSocket not initialized")
            
            # Place initial grid orders
            self._place_grid_orders()
            
            self.is_running = True
            
            # Start connection monitoring thread
            monitor_thread = Thread(target=self._monitor_connection, daemon=True)
            monitor_thread.start()
            
            self.logger.info("🚀 Grid Trading Bot started successfully with WebSocket monitoring")
            self.logger.info(f"📡 WebSocket Status: {'🟢 Connected' if (self.order_ws and self.order_ws.is_connected) else '🔴 Disconnected'}")
            
            # Keep main thread alive
            while self.is_running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("🛑 Keyboard interrupt received")
            raise  # Re-raise to be handled by main()
        except Exception as e:
            self.logger.error(f"❌ Error starting bot: {e}")
            raise  # Re-raise to be handled by main()
            
    def stop(self):
        """Stop the grid trading bot and cancel all orders"""
        self.logger.info("🛑 Stopping Grid Trading Bot...")
        self.is_running = False
        
        # Stop WebSocket first
        try:
            if self.order_ws:
                self.order_ws.stop()
                self.logger.info("📡 WebSocket stopped")
        except Exception as e:
            self.logger.error(f"❌ Error stopping WebSocket: {e}")
        
        # Cancel all orders with retry mechanism
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.lock:
                    active_orders = len([level for level in self.grid_levels if level.order_id])
                    if active_orders > 0:
                        self.logger.info(f"🔄 Cancelling {active_orders} active orders (attempt {attempt + 1}/{max_retries})...")
                        self._cancel_all_orders()
                        
                        # Verify orders were cancelled
                        time.sleep(1)  # Wait for cancellation to process
                        remaining_orders = self.client.ordersQuery(self.config.symbol)
                        if len(remaining_orders) == 0:
                            self.logger.info("✅ All orders successfully cancelled")
                            break
                        else:
                            self.logger.warning(f"⚠️ {len(remaining_orders)} orders still active")
                    else:
                        self.logger.info("✅ No active orders to cancel")
                        break
                        
            except Exception as e:
                self.logger.error(f"❌ Error cancelling orders (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    self.logger.error("❌ Failed to cancel orders after all retries")
                else:
                    time.sleep(2)  # Wait before retry
            
        self.logger.info("🏁 Grid Trading Bot stopped")
        
    def get_status(self) -> Dict:
        """Get bot status"""
        return {
            "is_running": self.is_running,
            "symbol": self.config.symbol,
            "current_price": float(self.current_price),
            "grid_count": len(self.grid_levels),
            "active_orders": len([level for level in self.grid_levels if level.order_id]),
            "websocket_connected": self.order_ws.is_connected if self.order_ws else False,
            "monitoring_method": "WebSocket Real-time"
        }


# Global bot instance for signal handling
_bot_instance = None

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print("\n🛑 Shutdown signal received. Stopping bot gracefully...")
    if _bot_instance:
        try:
            _bot_instance.stop()
            print("✅ Bot stopped successfully")
        except Exception as e:
            print(f"❌ Error during shutdown: {e}")
    sys.exit(0)


def load_config(config_name: str = "neutral") -> GridConfig:
    """Load configuration from JSON file"""
    try:
        with open("grid_config.json", "r") as f:
            configs = json.load(f)
            
        if config_name not in configs:
            raise ValueError(f"Configuration '{config_name}' not found")
            
        cfg = configs[config_name]
        return GridConfig(
            symbol=cfg["symbol"],
            base_sol_amount=Decimal(cfg["base_sol_amount"]),
            grid_count=cfg["grid_count"],
            grid_spread=Decimal(cfg["grid_spread"]),
            buy_grid_ratio=Decimal(str(cfg["buy_grid_ratio"])),
            sell_grid_ratio=Decimal(str(cfg["sell_grid_ratio"])),
            min_price=Decimal(cfg["min_price"]) if cfg["min_price"] else None,
            max_price=Decimal(cfg["max_price"]) if cfg["max_price"] else None,
            description=cfg.get("description", "Grid trading strategy"),
            api_key_env=cfg.get("api_key_env", "BACKPACK_API_KEY"),
            api_secret_env=cfg.get("api_secret_env", "BACKPACK_API_SECRET")
        )
    except Exception as e:
        print(f"Error loading config: {e}")
        return GridConfig()  # Return default config


def main():
    global _bot_instance
    
    # Load configuration (can specify 'neutral', 'long', or 'short')
    config_name = os.getenv('GRID_CONFIG', 'neutral')
    config = load_config(config_name)
    
    print(f"🚀 Using WebSocket Grid Bot configuration: {config_name}")
    print(f"📝 Description: {config.description}")
    print(f"📊 Symbol: {config.symbol}")
    print(f"💰 Base SOL Amount: {config.base_sol_amount}")
    print(f"🎯 Grid Count: {config.grid_count}")
    print(f"📈 Grid Spread: {config.grid_spread * 100}%")
    print(f"⚖️  Buy/Sell Ratio: {config.buy_grid_ratio*100:.0f}%/{config.sell_grid_ratio*100:.0f}%")
    print(f"📡 Monitoring: Real-time WebSocket (no polling delays!)")
    
    # Create bot and store global reference for signal handling
    bot = GridTradingBot(config)
    _bot_instance = bot
    
    # Register signal handlers AFTER creating bot instance
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        bot.start()
    except KeyboardInterrupt:
        print("\n🛑 Keyboard interrupt received")
        bot.stop()
    except Exception as e:
        print(f"❌ Bot error: {e}")
        bot.stop()
    finally:
        _bot_instance = None


if __name__ == "__main__":
    main()