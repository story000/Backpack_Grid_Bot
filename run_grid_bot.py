#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add current directory to Python path
sys.path.append(str(Path(__file__).parent))

from grid_trading_bot import GridTradingBot, GridConfig, load_config
from decimal import Decimal


def check_environment(config_name="default"):
    """Check if required environment variables are set for the specified config"""
    try:
        config = load_config(config_name)
        required_vars = [config.api_key_env, config.api_secret_env]
        missing_vars = []
        
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            print(f"❌ Missing required environment variables for '{config_name}' config:")
            for var in missing_vars:
                print(f"   - {var}")
            print("\nPlease set these in your .env file or export them:")
            for var in missing_vars:
                print(f"export {var}=your_value_here")
            return False
        
        print(f"✅ Environment variables configured for {config.symbol} ({config_name})")
        return True
    except Exception as e:
        print(f"❌ Error checking environment: {e}")
        return False


def dry_run_test(config_name="default"):
    """Test the grid calculation without placing orders"""
    print(f"\n📊 Running dry run test with '{config_name}' configuration...")
    
    # Load the specified configuration
    config = load_config(config_name)
    
    try:
        bot = GridTradingBot(config)
        bot._get_symbol_info()
        current_price = bot._get_current_price()
        
        print(f"Current {config.symbol} price: ${current_price}")
        
        # Calculate grid levels without placing orders
        bot._calculate_grid_levels()
        
        print(f"\nGrid levels calculated:")
        print(f"Price range: ${bot.config.min_price} - ${bot.config.max_price}")
        print(f"Grid count: {len(bot.grid_levels)}")
        
        print("\nGrid structure:")
        for i, level in enumerate(bot.grid_levels):
            order_type = "BUY " if level.is_buy else "SELL"
            base_asset = config.symbol.split('_')[0]  # Extract base asset from symbol
            print(f"  {i+1:2d}. {order_type} {level.quantity:>8} {base_asset} @ ${level.price}")
        
        return True
        
    except Exception as e:
        print(f"❌ Dry run failed: {e}")
        return False


def main():
    print("🤖 Multi-Pair Grid Trading Bot")
    print("=" * 40)
    
    # Show available configurations
    print("\n📋 Available configurations:")
    try:
        import json
        with open("grid_config.json", "r") as f:
            configs = json.load(f)
        
        for name, cfg in configs.items():
            buy_ratio = int(cfg['buy_grid_ratio'] * 100)
            sell_ratio = int(cfg['sell_grid_ratio'] * 100)
            base_asset = cfg['symbol'].split('_')[0]  # Extract base asset
            print(f"  • {name}: {cfg['symbol']} - {cfg['base_sol_amount']} {base_asset}, {cfg['grid_count']} grids, {buy_ratio}% BUY/{sell_ratio}% SELL")
            print(f"    └─ {cfg['description']}")
            print(f"    └─ API Keys: {cfg.get('api_key_env', 'BACKPACK_API_KEY')}, {cfg.get('api_secret_env', 'BACKPACK_API_SECRET')}")
    except Exception as e:
        print(f"Warning: Could not load configurations: {e}")
    
    # Get user choice
    print("\nOptions:")
    print("1. Run dry test (recommended first)")
    print("2. Start live trading")
    print("3. Exit")
    
    try:
        choice = input("\nEnter your choice (1-3): ").strip()
    except EOFError:
        choice = "1"  # Default to dry run in non-interactive mode
    
    if choice == "1":
        print("\nWhich configuration would you like to test?")
        try:
            import json
            with open("grid_config.json", "r") as f:
                configs = json.load(f)
            
            config_names = list(configs.keys())
            for i, name in enumerate(config_names, 1):
                cfg = configs[name]
                print(f"{i}. {name} - {cfg['symbol']} ({cfg['description']})")
        except Exception as e:
            print(f"Error loading configurations: {e}")
            return
        
        try:
            test_choice = input(f"Enter choice (1-{len(config_names)}) [1]: ").strip()
            if not test_choice:
                test_choice = "1"
            test_config = config_names[int(test_choice) - 1]
        except (EOFError, ValueError, IndexError):
            test_config = config_names[0]  # Default to first config
        
        # Check environment for selected config
        if not check_environment(test_config):
            print(f"\n❌ Environment check failed for '{test_config}' configuration.")
            return
            
        if dry_run_test(test_config):
            print(f"\n✅ Dry run with '{test_config}' configuration completed successfully!")
            print("You can now run live trading with option 2.")
        else:
            print("\n❌ Dry run failed. Please check your configuration.")
        
    elif choice == "2":
        print("\nAvailable configurations for live trading:")
        try:
            import json
            with open("grid_config.json", "r") as f:
                configs = json.load(f)
            
            config_names = list(configs.keys())
            for i, name in enumerate(config_names, 1):
                cfg = configs[name]
                print(f"{i}. {name} - {cfg['symbol']} ({cfg['description']})")
        except Exception as e:
            print(f"Error loading configurations: {e}")
            return
        
        try:
            live_choice = input(f"Enter choice (1-{len(config_names)}) [1]: ").strip()
            if not live_choice:
                live_choice = "1"
            config_name = config_names[int(live_choice) - 1]
        except (EOFError, ValueError, IndexError):
            config_name = config_names[0]  # Default to first config
        
        # Check environment for selected config
        if not check_environment(config_name):
            print(f"\n❌ Environment check failed for '{config_name}' configuration.")
            return
        
        print(f"\n🚀 Starting live trading with '{config_name}' configuration...")
        print("⚠️  WARNING: This will place real orders with real money!")
        confirm = input("Type 'YES' to confirm: ").strip()
        
        if confirm == "YES":
            try:
                config = load_config(config_name)
                bot = GridTradingBot(config)
                bot.start()
            except KeyboardInterrupt:
                bot.stop()
                print("\n👋 Bot stopped by user")
            except Exception as e:
                bot.stop()
                print(f"\n❌ Bot error: {e}")
        else:
            print("Cancelled.")
    
    elif choice == "3":
        print("👋 Goodbye!")
    
    else:
        print("Invalid choice.")


if __name__ == "__main__":
    main()