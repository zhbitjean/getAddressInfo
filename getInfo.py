import requests

def get_pluto_building_details(address):
    """
    输入地址，自动查出 PLUTO 数据库中的地块/建筑详细属性
    """
    print(f"🔎 正在解析地址: {address} ...")
    
    # 第一步：用官方 GeoSearch API 把文本地址转为 BBL (Borough, Block, Lot)
    geo_url = f"https://geosearch.planninglabs.nyc/v2/search?text={address}"
    geo_res = requests.get(geo_url).json()
    
    if not geo_res.get('features'):
        print(f"❌ 无法解析该地址: {address}")
        return None
    
    # 提取 BBL 属性
    props = geo_res['features'][0]['properties']
    bbl = props.get('addendum', {}).get('pad', {}).get('bbl') or props.get('bbl')
    
    if not bbl:
        print("❌ 无法获取该地址的 BBL")
        return None
        
    boro = bbl[0]
    block = str(int(bbl[1:6]))
    lot = str(int(bbl[6:10]))
    
    # 第二步：拿着 BBL 去查 NYC Open Data 的 PLUTO 数据库 (数据集 ID: 64uk-42ks)
    pluto_url = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
    pluto_params = {
        "$where": f"borocode={int(boro)} AND block={int(block)} AND lot={int(lot)}"
    }
    
    pluto_res = requests.get(pluto_url, params=pluto_params).json()
    
    if not pluto_res:
        print("❌ PLUTO 数据库中未找到该地块信息")
        return None
    
    data = pluto_res[0]
    
    # 第三步：映射并格式化你需要的属性字段
    building_info = {
        "Address": data.get("address", address),
        "BBL": data.get("bbl"),
        "Landmark Status": "L - LANDMARK" if data.get("landmark") else "No",
        "Land Use": data.get("landuse", "N/A"),
        "Lot Area": f"{data.get('lotarea', 'N/A')} sq ft",
        "Lot Frontage": f"{data.get('lotfront', 'N/A')} ft",
        "Lot Depth": f"{data.get('lotdepth', 'N/A')} ft",
        "Year Built": data.get("yearbuilt", "N/A"),
        "Year Altered": data.get("yearalter1", "N/A") if data.get("yearalter1") != "0" else "N/A",
        "Building Class": f"{data.get('bldgclass', 'N/A')}",
        "Units Res (Families)": data.get("unitsres", "N/A"),
        "Zoning Districts": data.get("zonedist1", "N/A")
    }
    
    return building_info

# --- 测试你给的 3 个地址 ---
addresses = [
    "35 EUCLID AVENUE, Brooklyn, NY",
    "220 LINCOLN ROAD, Brooklyn, NY",
    "185 KINGSTON AVENUE, Brooklyn, NY"
]

results = []
for addr in addresses:
    info = get_pluto_building_details(addr)
    if info:
        results.append(info)

# 打印对齐后的结果
print("\n" + "="*50)
print("📊 自动提取结果如下：")
print("="*50)
import json
print(json.dumps(results, indent=2, ensure_ascii=False))
