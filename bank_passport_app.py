# -*- coding: utf-8 -*-
import re
import io
import os
import time
from io import BytesIO
from typing import List, Dict
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Паспорт банковских условий", layout="wide")

# Cache directory
CACHE_DIR = Path("cache")
CACHE_FILE = CACHE_DIR / "last_passport.xlsx"
CACHE_INFO = CACHE_DIR / "last_load_info.txt"

# ------------------------- Helpers -------------------------

def save_to_cache(uploaded_file):
    """Save uploaded file to cache"""
    CACHE_DIR.mkdir(exist_ok=True)
    
    # Read file content into memory first
    file_content = uploaded_file.getvalue()
    
    # Use temporary file to avoid permission issues
    temp_file = CACHE_DIR / "temp_passport.xlsx"
    temp_info = CACHE_DIR / "temp_load_info.txt"
    
    # Write to temp file first
    with open(temp_file, "wb") as f:
        f.write(file_content)
    
    # Save info to temp file
    load_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with open(temp_info, "w", encoding="utf-8") as f:
        f.write(f"{load_time}\n{uploaded_file.name}")
    
    # Now replace the old files (Windows-safe way)
    time.sleep(0.2)  # Give time for any file handles to close
    
    # Remove old files if they exist
    for old_file in [CACHE_FILE, CACHE_INFO]:
        if old_file.exists():
            try:
                old_file.unlink()
            except PermissionError:
                time.sleep(0.3)
                try:
                    old_file.unlink()
                except:
                    pass  # If still can't delete, temp file will be used next time
    
    # Rename temp files to actual cache files
    try:
        temp_file.rename(CACHE_FILE)
        temp_info.rename(CACHE_INFO)
    except:
        pass  # Files will be picked up as temp files next time

def load_from_cache():
    """Load cached file if exists"""
    # Check for regular cache files first
    cache_file = CACHE_FILE
    info_file = CACHE_INFO
    
    # If regular files don't exist, check for temp files (from interrupted save)
    if not (cache_file.exists() and info_file.exists()):
        temp_cache = CACHE_DIR / "temp_passport.xlsx"
        temp_info = CACHE_DIR / "temp_load_info.txt"
        if temp_cache.exists() and temp_info.exists():
            cache_file = temp_cache
            info_file = temp_info
    
    if cache_file.exists() and info_file.exists():
        try:
            # Read info
            with open(info_file, "r", encoding="utf-8") as f:
                info = f.read().strip().split("\n")
                load_time = info[0] if len(info) > 0 else "неизвестно"
                file_name = info[1] if len(info) > 1 else "файл"
            
            # Read file into memory to avoid locking
            with open(cache_file, "rb") as f:
                file_bytes = f.read()
            
            # Load from BytesIO to avoid file locking
            df = pd.read_excel(BytesIO(file_bytes), sheet_name=0, header=0)
            df = df.dropna(axis=1, how="all")
            df.columns = [str(c).strip() for c in df.columns]
            
            return df, load_time, file_name
        except Exception as e:
            st.error(f"Ошибка загрузки кеша: {e}")
            return None, None, None
    return None, None, None

def load_excel(f) -> pd.DataFrame:
    try:
        if isinstance(f, (str, bytes)) or hasattr(f, "read"):
            df = pd.read_excel(f, sheet_name=0, header=0)
        else:
            df = pd.read_excel(f)
    except Exception as e:
        st.error(f"Ошибка чтения Excel: {e}")
        raise
    # Drop fully empty columns
    df = df.dropna(axis=1, how="all")
    # Standardize column names (strip spaces, unify)
    df.columns = [str(c).strip() for c in df.columns]
    return df

def find_col(df: pd.DataFrame, patterns: List[str]) -> str | None:
    cols = list(df.columns)
    for p in patterns:
        regex = re.compile(p, re.IGNORECASE)
        for c in cols:
            if regex.search(str(c)):
                return c
    return None

def detect_law_mentions(df: pd.DataFrame) -> Dict[str, List[int]]:
    # Scan all textual columns for known law labels
    law_labels = ["44-ФЗ", "223-ФЗ", "185-ФЗ", "615 ПП (ФКР)", "275-ФЗ", "115-ФЗ", "289-ФЗ", "505 ПП", "Иные"]
    law_map = {k: [] for k in law_labels}
    text_cols = [c for c in df.columns if df[c].dtype == "object"]
    
    # Map variations to canonical names
    law_variations = {
        "615 ПП (ФКР)": ["615", "615-пп", "пп 615", "пп рф 615", "фкр"],
        "44-ФЗ": ["44-фз", "44 фз"],
        "223-ФЗ": ["223-фз", "223 фз"],
        "185-ФЗ": ["185-фз", "185 фз"],
        "275-ФЗ": ["275-фз", "275 фз", "закрыт"],
        "115-ФЗ": ["115-фз", "115 фз", "концесс"],
        "289-ФЗ": ["289-фз", "289 фз", "таможен"],
        "505 ПП": ["505", "505-пп", "пп 505", "авансир"]
    }
    
    for idx, row in df.iterrows():
        cell_text = " | ".join(str(row[c]) for c in text_cols).lower()
        matched = False
        for law, variations in law_variations.items():
            if any(var in cell_text for var in variations):
                law_map[law].append(idx)
                matched = True
        if not matched:
            law_map["Иные"].append(idx)
    return law_map

def extract_stop_cols(df: pd.DataFrame) -> List[str]:
    # Columns containing STOP-like flags
    candidates = []
    for c in df.columns:
        cname = str(c).lower()
        if ("стоп" in cname) or ("stop" in cname) or ("запрет" in cname):
            candidates.append(c)
    # Also include typical boolean/flag columns with "услов"
    for c in df.columns:
        cname = str(c).lower()
        if "услов" in cname and df[c].nunique() <= 5:
            candidates.append(c)
    # Deduplicate preserving order
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

def extract_bg_type_cols(df: pd.DataFrame) -> List[str]:
    """Extract columns related to BG types"""
    candidates = []
    for c in df.columns:
        cname = str(c).lower()
        if any(x in cname for x in ["участие", "исполнен", "гарант", "возврат", "налог", "ндс", "платеж", "аренд", "коммерч", "оффсет"]):
            if "стоп" not in cname and c in df.columns:
                candidates.append(c)
    # Deduplicate
    seen = set()
    out = []
    for c in candidates:
        if c not in seen and c in df.columns:
            seen.add(c)
            out.append(c)
    return out

def extract_additional_condition_cols(df: pd.DataFrame) -> List[str]:
    """Extract columns for additional conditions"""
    candidates = []
    for c in df.columns:
        cname = str(c).lower()
        # Add columns for special conditions
        if any(x in cname for x in ["мультилот", "валют", "автоодобр", "переобеспеч", "закрыт", "концесс", "таможен"]):
            if "стоп" not in cname and c in df.columns:
                candidates.append(c)
    # Deduplicate
    seen = set()
    out = []
    for c in candidates:
        if c not in seen and c in df.columns:
            seen.add(c)
            out.append(c)
    return out

def normalize_bool_series(s: pd.Series) -> pd.Series:
    # Bring different truthy values to booleans
    def conv(x):
        if pd.isna(x):
            return False
        t = str(x).strip().lower()
        return t in {"1","true","истина","да","yes","y","д","✓","✔","ok","есть","вкл","on"}
    return s.apply(conv)

def filter_by_keyword(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query:
        return df
    pattern = re.escape(query.strip())
    mask = False
    for c in df.columns:
        ser = df[c]
        if ser.dtype == "object":
            mask = mask | ser.astype(str).str.contains(pattern, case=False, na=False, regex=True)
    return df[mask]

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Фильтр")
    return output.getvalue()

def extract_urls(text: str) -> List[str]:
    """Extract and clean URLs from text that may contain multiple URLs"""
    if not isinstance(text, str) or not text.strip():
        return []
    
    # Pattern to find URLs (http:// or https://)
    url_pattern = r'https?://[^\s<>"\'\)]*'
    urls = re.findall(url_pattern, text)
    
    # Clean and validate URLs
    cleaned = []
    for url in urls:
        url = url.strip()
        # Remove common trailing punctuation
        url = url.rstrip('.,;:)')
        if url and len(url) > 10:  # Basic validation
            cleaned.append(url)
    
    return cleaned

# ------------------------- UI -------------------------

st.title("Паспорт банковских условий")

with st.sidebar:
    st.header("Фильтры")

    # Data source
    st.markdown("### 📁 Загрузка данных")
    
    # Try to load from cache first
    cached_df, load_time, file_name = load_from_cache()
    
    if cached_df is not None:
        st.success(f"📂 Загружен: **{file_name}**")
        st.caption(f"Дата загрузки: {load_time}")
        
        # Option to clear cache
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Очистить", width="stretch"):
                # Remove all cache files (including temp files)
                cache_files = [
                    CACHE_FILE, 
                    CACHE_INFO,
                    CACHE_DIR / "temp_passport.xlsx",
                    CACHE_DIR / "temp_load_info.txt"
                ]
                for f in cache_files:
                    if f.exists():
                        try:
                            f.unlink()
                        except:
                            pass
                st.rerun()
        
        df = cached_df
    else:
        st.info("Загрузите файл Excel")
        df = None
    
    # File uploader (always visible)
    uploaded = st.file_uploader("Загрузить новый файл", type=["xlsx"], label_visibility="collapsed")
    
    if uploaded:
        # Load directly from uploaded file
        try:
            df = load_excel(uploaded)
            st.success(f"✅ **{uploaded.name}** загружен!")
            # Save to cache in background (non-blocking)
            try:
                save_to_cache(uploaded)
                st.info("💾 Файл сохранён в кеш и будет загружаться автоматически при следующем запуске")
            except Exception as cache_error:
                st.warning(f"⚠️ Файл загружен, но не сохранён в кеш: {cache_error}")
        except Exception as e:
            st.error(f"❌ Ошибка загрузки файла: {e}")
            df = None

if df is not None and len(df):
    # Column detection
    bank_col = find_col(df, [r"^банк", r"назв.*банк", r"наименование.*банк", r"банк$"])
    lim_deal_col = find_col(df, [r"лимит.*сделк", r"на\s*сделк", r"deal", r"per\s*deal", r"лимит.*операц"])
    lim_client_col = find_col(df, [r"лимит.*клиент", r"на\s*клиент", r"per\s*client"])
    url_col = find_col(df, [r"ссыл", r"url", r"заяв", r"application", r"link"])
    extra_req_col = find_col(df, [r"доп(\.|олнительн).*треб", r"требов", r"комментар"])

    # Sidebar controls
    with st.sidebar:
        # Basic filters
        st.markdown("### 🔍 Основные параметры")
        if bank_col:
            bank_filter = st.text_input("Банк (подстрока)", "")
        else:
            bank_filter = ""
        
        # Numeric limit filters if available
        lim_deal_min = lim_deal_max = None
        lim_client_min = lim_client_max = None

        if lim_deal_col and pd.api.types.is_numeric_dtype(df[lim_deal_col]):
            lim_deal_min, lim_deal_max = st.slider(
                f"{lim_deal_col}",
                float(df[lim_deal_col].min(skipna=True)),
                float(df[lim_deal_col].max(skipna=True)),
                (float(df[lim_deal_col].min(skipna=True)), float(df[lim_deal_col].max(skipna=True)))
            )

        if lim_client_col and pd.api.types.is_numeric_dtype(df[lim_client_col]):
            lim_client_min, lim_client_max = st.slider(
                f"{lim_client_col}",
                float(df[lim_client_col].min(skipna=True)),
                float(df[lim_client_col].max(skipna=True)),
                (float(df[lim_client_col].min(skipna=True)), float(df[lim_client_col].max(skipna=True)))
            )

        st.divider()

        # Laws
        st.markdown("### 📜 Законы")
        st.caption("Покажет банки с ЛЮБЫМ из выбранных законов:")
        law_map = detect_law_mentions(df)
        law_choices = [l for l in law_map.keys() if l != "Иные"]
        selected_laws = st.multiselect("Выберите законы", options=law_choices, default=[])

        st.divider()

        # BG Types
        st.markdown("### 🏦 Типы БГ")
        st.caption("Покажет банки с ЛЮБЫМ из выбранных типов:")
        bg_cols = extract_bg_type_cols(df)
        selected_bg_cols = []
        if bg_cols:
            for c in bg_cols:
                if st.checkbox(c, value=False, key=f"bg_{c}"):
                    selected_bg_cols.append(c)

        st.divider()

        # Additional conditions
        st.markdown("### ⚙️ Дополнительные условия")
        st.caption("Покажет банки с ЛЮБЫМ из выбранных условий:")
        add_cond_cols = extract_additional_condition_cols(df)
        selected_add_cond_cols = []
        if add_cond_cols:
            for c in add_cond_cols:
                if st.checkbox(c, value=False, key=f"cond_{c}"):
                    selected_add_cond_cols.append(c)

        st.divider()

        # STOP
        st.markdown("### ⛔ СТОП-условия (исключения)")
        st.caption("Покажет банки БЕЗ выбранных СТОП-условий (т.е. отфильтрует проблемные банки):")
        stop_cols = extract_stop_cols(df)
        selected_stop_cols = []
        for c in stop_cols:
            if st.checkbox(c, value=False, key=f"stop_{c}"):
                selected_stop_cols.append(c)

        st.divider()

        # Additional requirements
        st.markdown("### 📝 Поиск")
        extra_query = st.text_input("Доп. требования (подстрока)", "")
        q = st.text_input("Поиск по ключевым словам", "")

    # Apply filters
    filtered = df.copy()

    # Bank filter
    if bank_col and bank_filter.strip():
        filtered = filtered[filtered[bank_col].astype(str).str.contains(bank_filter.strip(), case=False, na=False)]

    # Limit filters
    if lim_deal_col and lim_deal_min is not None:
        filtered = filtered[filtered[lim_deal_col].between(lim_deal_min, lim_deal_max, inclusive="both")]
    if lim_client_col and lim_client_min is not None:
        filtered = filtered[filtered[lim_client_col].between(lim_client_min, lim_client_max, inclusive="both")]

    # Law filters (OR logic - показывать банки с ЛЮБЫМ из выбранных законов)
    if selected_laws:
        idx_sets = [set(law_map.get(law, [])) for law in selected_laws]
        if idx_sets:
            keep_idx = set.union(*idx_sets) if idx_sets else set()
            filtered = filtered.loc[filtered.index.intersection(list(keep_idx))]

    # BG type filters (OR logic - показывать банки с ЛЮБЫМ из выбранных типов БГ)
    if selected_bg_cols:
        mask = pd.Series([False] * len(filtered), index=filtered.index)
        for c in selected_bg_cols:
            if c in filtered.columns:
                mask = mask | normalize_bool_series(filtered[c])
        filtered = filtered[mask]

    # Additional condition filters (OR logic - показывать банки с ЛЮБЫМ из выбранных условий)
    if selected_add_cond_cols:
        mask = pd.Series([False] * len(filtered), index=filtered.index)
        for c in selected_add_cond_cols:
            if c in filtered.columns:
                mask = mask | normalize_bool_series(filtered[c])
        filtered = filtered[mask]

    # STOP condition filters (AND logic - исключаем банки с выбранными СТОП-условиями)
    # Если выбрано "СТОП регионы", показываем банки БЕЗ региональных ограничений
    for c in selected_stop_cols:
        if c in filtered.columns:
            # Инвертируем логику: показываем банки где СТОП = False (нет ограничения)
            filtered = filtered[~normalize_bool_series(filtered[c])]

    # Text search filters
    if extra_req_col and extra_query.strip():
        filtered = filtered[filtered[extra_req_col].astype(str).str.contains(re.escape(extra_query.strip()), case=False, na=False)]

    if q.strip():
        filtered = filter_by_keyword(filtered, q.strip())

    # Bank detail picker
    with st.container():
        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader("Таблица")
        with col2:
            st.metric("Найдено банков", len(filtered))
        
        # Build a compact view with optional 'Подробнее'
        view = filtered.copy()
        if bank_col and "Подробнее" not in view.columns:
            view.insert(0, "Подробнее", [f"Открыть:{i}" for i in view.index])

        st.dataframe(
            view,
            width="stretch",
            hide_index=True
        )

        # Bank selector
        bank_options = []
        if bank_col:
            bank_options = filtered[bank_col].dropna().astype(str).unique().tolist()
            bank_options = sorted(bank_options, key=lambda x: x.lower())

        st.divider()
        selected_bank = st.selectbox("Открыть карточку банка", options=["—"] + bank_options, index=0)

        if selected_bank != "—":
            row = filtered[filtered[bank_col].astype(str) == selected_bank].head(1)
            if not row.empty:
                r = row.iloc[0].to_dict()
                with st.expander(f"📋 Карточка банка: {selected_bank}", expanded=True):
                    st.markdown(f"### {selected_bank}")
                    
                    # Основные параметры
                    st.markdown("#### 📊 Основные параметры")
                    cols = st.columns(3)
                    with cols[0]:
                        if lim_deal_col:
                            st.metric("Лимит на сделку", r.get(lim_deal_col, '—'))
                    with cols[1]:
                        if lim_client_col:
                            st.metric("Лимит на клиента", r.get(lim_client_col, '—'))
                    with cols[2]:
                        max_term_col = find_col(df, [r"максимальн.*срок", r"срок.*бг", r"max.*term"])
                        if max_term_col:
                            st.metric("Макс. срок БГ", r.get(max_term_col, '—'))
                    
                    st.divider()
                    
                    # Законы и нормативы
                    st.markdown("#### 📜 Законы и нормативы")
                    law_cols = []
                    for c in df.columns:
                        cname = str(c).lower()
                        if any(x in cname for x in ["фз", "пп", "закон", "концесс", "закупк", "таможен"]):
                            if "стоп" not in cname:
                                law_cols.append(c)
                    
                    if law_cols:
                        law_col_chunks = [law_cols[i:i+4] for i in range(0, len(law_cols), 4)]
                        for chunk in law_col_chunks:
                            cols = st.columns(len(chunk))
                            for i, c in enumerate(chunk):
                                with cols[i]:
                                    val = r.get(c, '')
                                    is_yes = normalize_bool_series(pd.Series([val])).iloc[0]
                                    icon = "✅" if is_yes else "❌"
                                    st.write(f"{icon} **{c}**")
                    
                    st.divider()
                    
                    # Типы банковских гарантий
                    st.markdown("#### 🏦 Типы банковских гарантий")
                    bg_cols = []
                    for c in df.columns:
                        cname = str(c).lower()
                        if any(x in cname for x in ["участие", "исполнен", "гарант", "возврат", "налог", "ндс", "платеж", "аренд", "коммерч", "оффсет"]):
                            if "стоп" not in cname and c not in law_cols:
                                bg_cols.append(c)
                    
                    if bg_cols:
                        bg_col_chunks = [bg_cols[i:i+4] for i in range(0, len(bg_cols), 4)]
                        for chunk in bg_col_chunks:
                            cols = st.columns(len(chunk))
                            for i, c in enumerate(chunk):
                                with cols[i]:
                                    val = r.get(c, '')
                                    is_yes = normalize_bool_series(pd.Series([val])).iloc[0]
                                    icon = "✅" if is_yes else "❌"
                                    st.write(f"{icon} **{c}**")
                    
                    st.divider()
                    
                    # Дополнительные условия
                    st.markdown("#### ⚙️ Дополнительные условия")
                    cond_cols = []
                    for c in df.columns:
                        cname = str(c).lower()
                        if any(x in cname for x in ["мультилот", "валют", "автоодобр", "переобеспеч", "доставк", "самовыв", "оплата"]):
                            if "стоп" not in cname:
                                cond_cols.append(c)
                    
                    if cond_cols:
                        cond_col_chunks = [cond_cols[i:i+4] for i in range(0, len(cond_cols), 4)]
                        for chunk in cond_col_chunks:
                            cols = st.columns(len(chunk))
                            for i, c in enumerate(chunk):
                                with cols[i]:
                                    val = r.get(c, '')
                                    is_yes = normalize_bool_series(pd.Series([val])).iloc[0]
                                    icon = "✅" if is_yes else "❌"
                                    st.write(f"{icon} **{c}**")
                    
                    st.divider()
                    
                    # Организационно-правовые формы
                    st.markdown("#### 🏢 Организационно-правовые формы")
                    opf_cols = []
                    for c in df.columns:
                        cname = str(c).lower()
                        if any(x in cname for x in ["ооо", "оао", "зао", "пао", "фгуп", "муп", "гуп", "ано"]) or \
                           (("ип" in cname or "опф" in cname or "форм" in cname) and "стоп" not in cname):
                            opf_cols.append(c)
                    
                    if opf_cols:
                        opf_col_chunks = [opf_cols[i:i+4] for i in range(0, len(opf_cols), 4)]
                        for chunk in opf_col_chunks:
                            cols = st.columns(len(chunk))
                            for i, c in enumerate(chunk):
                                with cols[i]:
                                    val = r.get(c, '')
                                    is_yes = normalize_bool_series(pd.Series([val])).iloc[0]
                                    icon = "✅" if is_yes else "❌"
                                    st.write(f"{icon} **{c}**")
                    
                    st.divider()
                    
                    # СТОП-условия
                    st.markdown("#### ⛔ СТОП-условия")
                    if stop_cols:
                        items_yes = []
                        items_no = []
                        for c in stop_cols:
                            val = r.get(c, None)
                            truthy = normalize_bool_series(pd.Series([val])).iloc[0]
                            if truthy:
                                items_yes.append(f"🔴 {c}")
                            else:
                                items_no.append(f"🟢 {c}")
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if items_yes:
                                st.markdown("**❌ Есть ограничения:**")
                                st.markdown("\n".join(items_yes))
                            else:
                                st.success("✅ Нет ограничений!")
                        with col2:
                            if items_no:
                                st.markdown("**✅ Нет ограничений:**")
                                st.markdown("\n".join(items_no))
                    
                    st.divider()
                    
                    # Дополнительная информация
                    st.markdown("#### 📝 Дополнительная информация")
                    
                    # Требования к организации
                    org_req_col = find_col(df, [r"срок.*действ.*орган", r"возраст.*компан"])
                    if org_req_col:
                        st.write(f"**{org_req_col}:** {r.get(org_req_col, '—')}")
                    
                    # Дополнительные требования
                    if extra_req_col:
                        req_text = r.get(extra_req_col, '')
                        if req_text and str(req_text).strip() and str(req_text).lower() not in ['nan', 'none', '—']:
                            st.write(f"**Доп. требования:**")
                            st.info(req_text)
                    
                    # Фишки банка
                    feat_col = find_col(df, [r"фишк", r"особен", r"преимущест"])
                    if feat_col:
                        feat_text = r.get(feat_col, '')
                        if feat_text and str(feat_text).strip() and str(feat_text).lower() not in ['nan', 'none', '—']:
                            st.write(f"**Фишки банка:**")
                            st.success(feat_text)
                    
                    # Ссылки
                    if url_col:
                        link_text = r.get(url_col, "")
                        urls = extract_urls(str(link_text))
                        if urls:
                            st.markdown("#### 🔗 Подать заявку")
                            for i, url in enumerate(urls, 1):
                                label = "Подать заявку" if len(urls) == 1 else f"Подать заявку #{i}"
                                st.link_button(label, url=url, width="stretch")

    # Export
    st.subheader("Экспорт")
    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    xlsx_bytes = to_excel_bytes(filtered)

    st.download_button("Скачать CSV", data=csv_bytes, file_name="фильтр_банки.csv", mime="text/csv")
    st.download_button("Скачать Excel", data=xlsx_bytes, file_name="фильтр_банки.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.info("📁 Загрузите файл Excel в боковой панели слева")
