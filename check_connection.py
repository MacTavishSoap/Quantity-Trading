
import ccxt
import sys
import os

def check_connection():
    print("🔍 开始网络连接诊断...")
    
    # 1. 尝试直连
    print("\n[1] 尝试直连 (无代理)...")
    try:
        exchange_direct = ccxt.okx({
            'timeout': 5000,
            'enableRateLimit': True,
        })
        exchange_direct.fetch_time()
        print("✅ 直连成功！您的网络可以直接访问 OKX。")
        return "direct"
    except Exception as e:
        print(f"❌ 直连失败: {str(e)[:100]}...")

    # 2. 尝试本地代理 127.0.0.1:7890
    print("\n[2] 尝试本地代理 (http://127.0.0.1:7890)...")
    try:
        exchange_proxy = ccxt.okx({
            'timeout': 5000,
            'enableRateLimit': True,
            'proxies': {
                'http': 'http://127.0.0.1:7890',
                'https': 'http://127.0.0.1:7890',
            }
        })
        exchange_proxy.fetch_time()
        print("✅ 代理连接成功！")
        return "proxy_7890"
    except Exception as e:
        print(f"❌ 代理连接失败: {str(e)[:100]}...")

    print("\n⚠️ 无法连接到 OKX API。")
    print("请检查：")
    print("1. 是否开启了 VPN/代理软件？")
    print("2. 代理端口是否为 7890？")
    return None

if __name__ == "__main__":
    result = check_connection()
    if result:
        print(f"\n💡 建议配置: {result}")
    else:
        sys.exit(1)
