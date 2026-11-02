# -*- coding: utf-8 -*-
import streamlit as st
import warnings
warnings.filterwarnings("ignore")

import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker
import matplotlib.patheffects as pe
import os
import tempfile
import io
import itertools
import requests
from matplotlib_scalebar.scalebar import ScaleBar

st.set_page_config(page_title="专业地图出图控制台", layout="wide")
st.title("🖨️ 专业地图静态出图控制台 (终极防呆版)")

# ====== 数据防丢 ======
if "river_files" not in st.session_state: st.session_state.river_files = None
if "excel_file" not in st.session_state: st.session_state.excel_file = None

st.sidebar.header("1. 上传数据文件")
st.sidebar.info("💡 彻底解决 clip 裁剪失败导致河流消失的问题！")

river_uploads = st.sidebar.file_uploader("上传水系 SHP", accept_multiple_files=True)
if river_uploads: st.session_state.river_files = river_uploads
elif st.session_state.river_files: river_uploads = st.session_state.river_files

excel_upload = st.sidebar.file_uploader("上传点位 Excel", type=["xlsx", "xls"])
if excel_upload: st.session_state.excel_file = excel_upload
elif st.session_state.excel_file: excel_upload = st.session_state.excel_file

st.sidebar.header("2. 出图选项")
title_text = st.sidebar.text_input("地图标题", "空间设施及水系分布示意图")
show_names = st.sidebar.checkbox("显示行政区划名称", value=True) 
show_point_names = st.sidebar.checkbox("在地图上标出点位名称", value=True) 

# ====== 数据读取 (去掉静默pass，全部暴露出来) ======
river_gdf, df = None, None

if river_uploads:
    r_temp_dir = tempfile.mkdtemp()
    for f in river_uploads: 
        with open(os.path.join(r_temp_dir, f.name), "wb") as out: out.write(f.getbuffer())
    r_shp_path = [os.path.join(r_temp_dir, f.name) for f in river_uploads if f.name.endswith('.shp')][0]
    
    try:
        river_gdf = gpd.read_file(r_shp_path)
        if river_gdf.crs is None: 
            river_gdf.set_crs(epsg=4326, inplace=True)
        elif river_gdf.crs.to_epsg() != 4326: 
            river_gdf = river_gdf.to_crs(epsg=4326)
        
        # 强行修复几何体，防止 clip 崩溃
        river_gdf['geometry'] = river_gdf.geometry.buffer(0)
        river_gdf = river_gdf[~river_gdf.geometry.is_empty].dropna(subset=['geometry'])
        
        st.sidebar.success(f"✅ 水系加载成功，共 {len(river_gdf)} 个要素")
        st.sidebar.write(f"几何类型: {river_gdf.geom_type.unique()}")
        st.sidebar.write(f"经度范围: {river_gdf.total_bounds[0]:.2f} ~ {river_gdf.total_bounds[2]:.2f}")
        st.sidebar.write(f"纬度范围: {river_gdf.total_bounds[1]:.2f} ~ {river_gdf.total_bounds[3]:.2f}")
    except Exception as e:
        st.sidebar.error(f"水系读取失败: {e}")

if excel_upload:
    try:
        df = pd.read_excel(excel_upload)
    except Exception as e:
        st.sidebar.error(f"Excel读取失败: {e}")

# ====== 控制面板 ======
if df is not None:
    cols = df.columns.tolist()
    st.sidebar.markdown("---")
    st.sidebar.header("3. 表格列名映射")
    lon_col = st.sidebar.selectbox("经度列", cols, index=cols.index(next((c for c in cols if '经' in str(c) or 'lon' in str(c).lower()), cols[0])))
    lat_col = st.sidebar.selectbox("纬度列", cols, index=cols.index(next((c for c in cols if '纬' in str(c) or 'lat' in str(c).lower()), cols[0])))
    type_col = st.sidebar.selectbox("点位类型列", cols, index=cols.index(next((c for c in cols if '类' in str(c) or '型' in str(c)), cols[0])))
    name_col = st.sidebar.selectbox("名称注记列 (可选)", [None] + cols)

st.sidebar.markdown("---")
st.sidebar.header("4. 图层控制")
show_rivers = st.sidebar.checkbox("显示水系", value=True)

selected_orders = None
if river_gdf is not None:
    if 'HLJB' in river_gdf.columns:
        clean_series = river_gdf['HLJB'].astype(str).str.replace('.0', '', regex=False)
        river_gdf['HLJB'] = clean_series
        unique_orders = sorted([x for x in clean_series.dropna().unique() if x != 'None' and x != 'nan'])
        # 默认全选，不管它是 1,2 还是 g,z1
        selected_orders = st.sidebar.multiselect("勾选显示的河流级别 (HLJB)", unique_orders, default=unique_orders)
    else:
        selected_orders = None

@st.cache_data(show_spinner=False)
def fetch_county_boundaries(lon, lat):
    try:
        url_city = "https://geo.datav.aliyun.com/areas_v3/bound/450000_full.json"
        res_city = requests.get(url_city, timeout=10).json()
        gdf_city = gpd.GeoDataFrame.from_features(res_city['features'])
        
        pt = gpd.GeoDataFrame(geometry=gpd.points_from_xy([lon], [lat]), crs="EPSG:4326")
        joined_city = gpd.sjoin(pt, gdf_city, how="inner", predicate="intersects")
        city_code = joined_city.iloc[0]['adcode'] if len(joined_city) > 0 else "450200"
            
        url_county = f"https://geo.datav.aliyun.com/areas_v3/bound/{city_code}_full.json"
        res_county = requests.get(url_county, timeout=10).json()
        gdf_county = gpd.GeoDataFrame.from_features(res_county['features'])
        gdf_county.set_crs(epsg=4326, inplace=True)
        return gdf_county
    except Exception:
        return None

# ====== 实时渲染 ======
if df is not None:
    try:
        gdf_pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs="EPSG:4326")
        center_lon = gdf_pts.geometry.x.mean()
        center_lat = gdf_pts.geometry.y.mean()
        
        with st.spinner("正在渲染精修地图..."):
            gdf_bound_county = fetch_county_boundaries(center_lon, center_lat)
            
            if gdf_bound_county is not None:
                joined_county = gpd.sjoin(gdf_pts, gdf_bound_county, how="inner", predicate="intersects")
                if len(joined_county) > 0:
                    active_indices = joined_county.index_right.unique()
                    gdf_bound_filtered = gdf_bound_county.loc[active_indices].copy()
                else:
                    gdf_bound_filtered = gdf_bound_county.copy()

                plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei'] 
                plt.rcParams['axes.unicode_minus'] = False
                fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
                
                base_colors = ['#F0F8FF', '#FFF0F5', '#F5FFFA', '#FFFACD', '#E6E6FA', '#FFE4E1', '#E0FFFF']
                color_cycle = itertools.cycle(base_colors)
                plot_colors = [next(color_cycle) for _ in range(len(gdf_bound_filtered))]
                
                gdf_bound_filtered.plot(ax=ax, color=plot_colors, edgecolor='#999999', linewidth=1.0, alpha=0.8)
                
                # 行政区名字
                if show_names:
                    for idx, row in gdf_bound_filtered.iterrows():
                        if 'name' in row and pd.notna(row['name']):
                            c = row.geometry.centroid
                            ax.text(c.x, c.y, str(row['name']), fontsize=12, color='#555555', ha='center', va='center', fontweight='bold')
                
                # 🚨 核心修复：不强制裁剪！如果裁剪失败，直接全画
                if river_gdf is not None and show_rivers:
                    # 先尝试裁剪
                    union_poly = gdf_bound_filtered.geometry.unary_union
                    river_clipped = gpd.clip(river_gdf, union_poly)
                    
                    # 如果裁剪后是空的，退回到使用原始的 river_gdf
                    if len(river_clipped) == 0:
                        st.sidebar.warning("⚠️ 裁剪水系为空（可能是坐标系偏移或不在该区域），已退回全量绘制。")
                        river_to_draw = river_gdf.copy()
                    else:
                        river_to_draw = river_clipped
                    
                    if selected_orders is not None and len(selected_orders) > 0:
                        river_to_draw = river_to_draw[river_to_draw['HLJB'].isin(selected_orders)]
                    
                    # 1. 画面
                    poly_gdf = river_to_draw[river_to_draw.geom_type.isin(['Polygon', 'MultiPolygon'])]
                    if len(poly_gdf) > 0:
                        poly_gdf['area'] = poly_gdf.geometry.area
                        try:
                            poly_gdf['level'] = pd.qcut(poly_gdf['area'].rank(method='first'), q=3, labels=[1, 2, 3])
                        except:
                            poly_gdf['level'] = 2 
                        
                        for lvl, color in zip([1, 2, 3], ['#3A86FF', '#5FA8D3', '#A2D2FF']):
                            sub_poly = poly_gdf[poly_gdf['level'] == lvl]
                            if len(sub_poly) > 0:
                                sub_poly.plot(ax=ax, color=color, edgecolor='none', alpha=1.0, zorder=3)
                    
                    # 2. 画线
                    line_gdf = river_to_draw[river_to_draw.geom_type.isin(['LineString', 'MultiLineString'])]
                    if len(line_gdf) > 0:
                        line_gdf.plot(ax=ax, color='#90E0EF', linewidth=1.2, zorder=2)
                
                # 画点位
                marker_styles = ['o', '^', 's', 'D', 'v', 'p'] 
                auto_colors = ['#ff3333', '#0033cc', '#33cc33', '#ff9900', '#9933cc']
                unique_types = gdf_pts[type_col].unique()
                
                for i, p_type in enumerate(unique_types):
                    subset = gdf_pts[gdf_pts[type_col] == p_type]
                    ax.scatter(subset.geometry.x, subset.geometry.y, c=auto_colors[i%len(auto_colors)], s=60, marker=marker_styles[i%len(marker_styles)], edgecolors='white', linewidth=1.0, label=f'{p_type} ({len(subset)}个)', zorder=5)
                    
                    if show_point_names and name_col:
                        for idx, row in subset.iterrows():
                            t = ax.text(
                                row.geometry.x + 0.003, row.geometry.y + 0.003, 
                                str(row[name_col]), 
                                fontsize=7, 
                                color='#D32F2F', 
                                fontweight='bold',
                                zorder=6
                            )
                            t.set_path_effects([pe.withStroke(linewidth=2, foreground='white')])

                # 视野锁定
                minx, miny, maxx, maxy = gdf_bound_filtered.total_bounds
                pad_x = (maxx - minx) * 0.1 if (maxx - minx) != 0 else 0.05
                pad_y = (maxy - miny) * 0.1 if (maxy - miny) != 0 else 0.05

                ax.set_xlim(minx - pad_x, maxx + pad_x)
                ax.set_ylim(miny - pad_y, maxy + pad_y)
                ax.set_aspect('equal')
                
                ax.xaxis.set_major_locator(ticker.MultipleLocator(0.1))
                ax.yaxis.set_major_locator(ticker.MultipleLocator(0.1))
                ax.grid(linestyle='--', linewidth=0.5, color='#cccccc', alpha=0.6)
                ax.tick_params(direction='in', top=True, right=True)
                ax.set_title(f"{title_text}\n", fontsize=16, fontweight='bold')
                
                handles, labels = ax.get_legend_handles_labels()
                handles.append(mpatches.Patch(facecolor='#E0FFFF', edgecolor='#999', label='行政区划'))
                handles.append(mpatches.Patch(facecolor='#3A86FF', edgecolor='none', label='主要河流 (实心高亮)'))
                handles.append(mpatches.Patch(facecolor='#A2D2FF', edgecolor='none', label='次级河流 (实心高亮)'))
                ax.legend(handles=handles, loc='lower left', title="图例")
                
                stats = f"监测点位总数：{len(gdf_pts)} 个\n"
                for p_type in unique_types:
                    stats += f"{p_type}：{len(gdf_pts[gdf_pts[type_col] == p_type])} 个\n"
                ax.text(0.98, 0.02, stats, transform=ax.transAxes, ha='right', va='bottom', bbox=dict(boxstyle="round,pad=0.5", fc="#F8F9FA", ec="#CCCCCC", alpha=0.9))
                
                plt.tight_layout()
                buf = io.BytesIO()
                plt.savefig(buf, format="png", dpi=300, bbox_inches="tight")
                buf.seek(0)
                plt.close(fig)
                
                st.success("✅ 图像已更新！")
                st.download_button("📥 下载当前效果图.png", data=buf, file_name="最终效果图.png", mime="image/png")
                st.image(buf, use_container_width=True)

    except Exception as e:
        st.error(f"渲染失败: {str(e)}")
else:
    st.info("👈 请先在左侧上传点位 Excel。")