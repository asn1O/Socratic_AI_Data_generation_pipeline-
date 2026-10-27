#!/usr/bin/env python3
"""
generate_distribution_graphs.py

Parses data_distribution.log and generates publication-grade visualizations
for technical reports, presentations, and publications.
"""

import os
import re
import shutil
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns

# Set overall style
plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.labelweight": "bold",
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.titlesize": 15,
    "figure.titleweight": "bold",
    "figure.autolayout": False,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

# Color Constants
COLORS_3CAT = ["#3B82F6", "#F59E0B", "#EF4444"]
PROVIDER_COLORS = {
    "Google": "#4285F4",
    "NVIDIA": "#76B900",
    "OpenRouter": "#8B5CF6"
}

def parse_log_file(log_path: Path):
    text = log_path.read_text(encoding="utf-8")

    # 1. Total summary
    m_tot = re.search(r"Total Conversations Analyzed:\s*(\d+)", text)
    if not m_tot:
        raise ValueError("Could not parse 'Total Conversations Analyzed' from log file.")
    total_convs = int(m_tot.group(1))

    # Split Sections to avoid leakage
    sec1, sec2 = text.split("# SECTION 2: DOMAIN DISTRIBUTION")

    # 2. Config Parameters (from sec1)
    param_blocks = re.split(r"CONFIG PARAMETER:\s*(\w+)", sec1)
    params_data = {}
    for i in range(1, len(param_blocks), 2):
        pname = param_blocks[i]
        pcontent = param_blocks[i+1]
        overall_match = re.search(r"OVERALL DATASET TOTAL:.*?conversations\s*((?:\s+•\s+[\w_]+\s+:\s+[\d,]+\s+\(\s*[\d\.]+%\)\n)+)", pcontent)
        overall = {}
        if overall_match:
            for line in overall_match.group(1).strip().split("\n"):
                m = re.search(r"•\s*(\w+)\s*:\s*([\d,]+)\s*\(\s*([\d\.]+)%\)", line)
                if m:
                    overall[m.group(1)] = {"count": int(m.group(2).replace(",", "")), "pct": float(m.group(3))}
        
        folders = {}
        parts = re.split(r"\[\s*(.*?)\s*\]\s*\n\s*Folder Total:\s*(\d+)[^\n]*\n", pcontent)
        for j in range(1, len(parts), 3):
            fname = parts[j]
            ftotal = int(parts[j+1])
            fchunk = parts[j+2]
            fdict = {"total": ftotal, "values": {}}
            for line in fchunk.strip().split("\n"):
                m = re.search(r"•\s*(\w+)\s*:\s*([\d,]+)\s*\(\s*([\d\.]+)%\)", line)
                if m:
                    fdict["values"][m.group(1)] = {"count": int(m.group(2).replace(",", "")), "pct": float(m.group(3))}
            folders[fname] = fdict
        params_data[pname] = {"overall": overall, "folders": folders}

    # 3. Domain Distribution (from sec2)
    overall_match = re.search(r"OVERALL DATASET TOTAL:.*?conversations\s*((?:\s+•\s+[\w_]+\s+:\s+[\d,]+\s+\(\s*[\d\.]+%\)\n)+)", sec2)
    overall_domains = {}
    if overall_match:
        for line in overall_match.group(1).strip().split("\n"):
            m = re.search(r"•\s*([\w_]+)\s*:\s*([\d,]+)\s*\(\s*([\d\.]+)%\)", line)
            if m:
                overall_domains[m.group(1)] = {"count": int(m.group(2).replace(",", "")), "pct": float(m.group(3))}

    folder_domains = {}
    parts = re.split(r"\[\s*(.*?)\s*\]\s*\n\s*Folder Total:\s*(\d+)[^\n]*\n", sec2)
    for j in range(1, len(parts), 3):
        fname = parts[j]
        ftotal = int(parts[j+1])
        fchunk = parts[j+2]
        fdict = {"total": ftotal, "domains": {}}
        for line in fchunk.strip().split("\n"):
            m = re.search(r"•\s*([\w_]+)\s*:\s*([\d,]+)\s*\(\s*([\d\.]+)%\)", line)
            if m:
                fdict["domains"][m.group(1)] = {"count": int(m.group(2).replace(",", "")), "pct": float(m.group(3))}
        folder_domains[fname] = fdict

    # 4. Folder totals & providers
    folders_summary = {}
    for fname, fdict in folder_domains.items():
        total = fdict["total"]
        if fname.startswith("Google_"):
            provider = "Google"
            model_name = fname.replace("Google_", "")
        elif fname.startswith("NVIDIA_"):
            provider = "NVIDIA"
            model_name = fname.replace("NVIDIA_", "")
        elif fname.startswith("OpenRouter_"):
            provider = "OpenRouter"
            model_name = fname.replace("OpenRouter_", "")
        else:
            provider = "Other"
            model_name = fname
        folders_summary[fname] = {
            "folder_name": fname,
            "provider": provider,
            "model_name": model_name,
            "total": total,
            "pct": (total / total_convs) * 100
        }

    return total_convs, params_data, overall_domains, folder_domains, folders_summary


def clean_domain_label(raw: str) -> str:
    mapping = {
        "economics_and_finance": "Economics & Finance",
        "statistics_and_data": "Statistics & Data",
        "physics": "Physics",
        "quantitative_aptitude": "Quantitative Aptitude",
        "chemistry": "Chemistry",
        "logic_and_puzzles": "Logic & Puzzles",
        "ethics_and_critical_thinking": "Ethics & Critical Thinking",
        "biology": "Biology",
        "computer_science_light": "Computer Science (Light)",
        "history_and_social_studies": "History & Social Studies",
        "language_and_grammar": "Language & Grammar",
        "everyday_reasoning": "Everyday Reasoning",
    }
    return mapping.get(raw, raw.replace("_", " ").title())


def plot_fig1_config_parameters(total_convs, params_data, out_path: Path):
    """Plot 2x3 grid of config parameter distributions showing balanced 3-way split."""
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), dpi=300)
    fig.suptitle(f"Dataset Uniformity Across 6 Socratic Configuration Parameters (N = {total_convs:,})", 
                 fontsize=16, fontweight="bold", y=0.98)

    param_meta = [
        ("socratic_level", "Socratic Level", ["low", "medium", "high"]),
        ("student_level", "Student Level", ["beginner", "intermediate", "advanced"]),
        ("hint_frequency", "Hint Frequency", ["low", "medium", "high"]),
        ("mistake_frequency", "Mistake Frequency", ["low", "medium", "high"]),
        ("verbosity", "Verbosity", ["low", "medium", "high"]),
        ("difficulty", "Problem Difficulty", ["easy", "medium", "hard"]),
    ]

    for idx, (pname, title, order) in enumerate(param_meta):
        ax = axes[idx // 3, idx % 3]
        data = params_data[pname]["overall"]
        
        counts = [data.get(cat, {}).get("count", 0) for cat in order]
        pcts = [data.get(cat, {}).get("pct", 0.0) for cat in order]
        labels = [cat.capitalize() for cat in order]

        bars = ax.bar(labels, pcts, color=COLORS_3CAT, width=0.55, edgecolor="none", zorder=3)
        
        # Add baseline expected 33.3% line
        ax.axhline(33.33, color="#64748B", linestyle="--", linewidth=1.2, zorder=4, label="Target (33.3%)")
        
        # Clean white labels inside the bar
        for bar, count, pct in zip(bars, counts, pcts):
            yval = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2.0, yval - 5.5,
                    f"{pct:.1f}%\n({count:,})",
                    ha="center", va="center", fontsize=10, fontweight="bold", color="white", zorder=5)

        ax.set_title(title, fontsize=12, pad=10)
        ax.set_ylim(0, 44)
        ax.set_ylabel("Share (%)" if idx % 3 == 0 else "")
        ax.yaxis.set_major_formatter(ticker.PercentFormatter(xmax=100, decimals=0))
        ax.grid(axis="y", linestyle=":", alpha=0.6, zorder=1)
        if idx == 0:
            ax.legend(loc="upper left", frameon=True, fontsize=8.5)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig2_domain_distribution(total_convs, overall_domains, out_path: Path):
    """Plot horizontal bar chart of the 12 domains showing high balance."""
    sorted_domains = sorted(overall_domains.items(), key=lambda x: x[1]["count"], reverse=True)
    labels = [clean_domain_label(k) for k, v in sorted_domains]
    counts = [v["count"] for k, v in sorted_domains]
    pcts = [v["pct"] for k, v in sorted_domains]

    fig, ax = plt.subplots(figsize=(12, 7.5), dpi=300)
    
    cmap = sns.color_palette("Blues_r", n_colors=len(labels) + 4)
    colors = cmap[:len(labels)]

    y_pos = np.arange(len(labels))
    bars = ax.barh(y_pos, counts, align="center", color=colors, height=0.68, edgecolor="none", zorder=3)
    
    target_count = total_convs / 12.0
    ax.axvline(target_count, color="#DC2626", linestyle="--", linewidth=1.5, zorder=4,
               label=f"Ideal Uniform Target ({target_count:.1f} convs / 8.33%)")

    for bar, count, pct in zip(bars, counts, pcts):
        xval = bar.get_width()
        ax.text(xval + 4, bar.get_y() + bar.get_height()/2.0,
                f"{count:,}  ({pct:.1f}%)",
                ha="left", va="center", fontsize=9.5, fontweight="bold", color="#1E293B")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10.5)
    ax.invert_yaxis()
    ax.set_xlabel("Number of Conversations", labelpad=8)
    ax.set_xlim(0, 370)
    ax.set_title(f"Domain Distribution Across 12 Knowledge Disciplines (N = {total_convs:,})\n"
                 "Demonstrating Highly Balanced Multi-Discipline Synthetic Coverage",
                 pad=15, fontsize=13)
    ax.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)
    ax.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="#CBD5E1", fontsize=10)

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig3_model_generator_distribution(total_convs, folders_summary, out_path: Path):
    """Plot conversation count by generator model with provider color coding."""
    items = sorted(folders_summary.values(), key=lambda x: x["total"], reverse=True)
    items = [x for x in items if x["total"] > 0]

    names = [x["model_name"] for x in items]
    counts = [x["total"] for x in items]
    pcts = [x["pct"] for x in items]
    providers = [x["provider"] for x in items]
    colors = [PROVIDER_COLORS.get(p, "#94A3B8") for p in providers]

    fig, ax = plt.subplots(figsize=(13, 8.5), dpi=300)
    y_pos = np.arange(len(names))
    bars = ax.barh(y_pos, counts, align="center", color=colors, height=0.65, edgecolor="none", zorder=3)

    for bar, count, pct in zip(bars, counts, pcts):
        xval = bar.get_width()
        txt = f"{count:,} ({pct:.1f}%)" if count > 5 else f"{count} ({pct:.1f}%)"
        ax.text(xval + 5, bar.get_y() + bar.get_height()/2.0,
                txt, ha="left", va="center", fontsize=9, fontweight="bold", color="#1E293B")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=9.5)
    ax.invert_yaxis()
    ax.set_xlabel("Total Conversations Generated", labelpad=8)
    ax.set_xlim(0, 500)
    ax.set_title(f"Conversation Volume by Generator Model & Platform (N = {total_convs:,})\n"
                 "Multi-LLM Synthetic Socratic Dialog Generation", pad=15, fontsize=13)
    ax.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)

    from matplotlib.patches import Patch
    provider_counts = {}
    for item in folders_summary.values():
        p = item["provider"]
        provider_counts[p] = provider_counts.get(p, 0) + item["total"]

    provider_labels_map = {
        "Google": "Google Gemini / Gemma",
        "NVIDIA": "NVIDIA NIM API",
        "OpenRouter": "OpenRouter API",
    }
    legend_elements = [
        Patch(
            facecolor=PROVIDER_COLORS[p],
            label=f"{provider_labels_map.get(p, p)} ({provider_counts.get(p, 0):,} convs, {provider_counts.get(p, 0) / total_convs * 100:.1f}%)"
        )
        for p in ["Google", "NVIDIA", "OpenRouter"] if p in provider_counts
    ]
    ax.legend(handles=legend_elements, loc="lower right", frameon=True, fontsize=10, facecolor="white")

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig4_provider_and_model_hierarchy(total_convs, folders_summary, out_path: Path):
    """Plot provider breakdown donut chart and model breakdown side by side."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.5), dpi=300)
    fig.suptitle("Generator Ecosystem: Provider Market Share & Architecture Breakdown",
                 fontsize=15, fontweight="bold", y=0.98)

    provider_counts = {}
    for item in folders_summary.values():
        p = item["provider"]
        provider_counts[p] = provider_counts.get(p, 0) + item["total"]

    prov_labels = [p for p in ["Google", "NVIDIA", "OpenRouter"] if p in provider_counts]
    prov_vals = [provider_counts[p] for p in prov_labels]
    prov_colors = [PROVIDER_COLORS[p] for p in prov_labels]

    wedges, texts, autotexts = ax1.pie(
        prov_vals,
        labels=[f"{p}\n({provider_counts[p]:,} convs)" for p in prov_labels],
        autopct="%1.1f%%",
        pctdistance=0.75,
        startangle=140,
        colors=prov_colors,
        wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2.5),
        textprops=dict(fontsize=10.5)
    )
    for at in autotexts:
        at.set_fontsize(11)
        at.set_fontweight("bold")
        at.set_color("white")
    ax1.set_title("Share by API Provider / Platform", pad=10, fontsize=12)

    ax1.text(0, 0, f"Total\n{total_convs:,}\nConversations", ha="center", va="center",
             fontsize=11.5, fontweight="bold", color="#1E293B")

    top_models = sorted(folders_summary.values(), key=lambda x: x["total"], reverse=True)[:8]
    m_names = [f"{x['model_name'][:28]}..." if len(x['model_name']) > 28 else x['model_name'] for x in top_models]
    m_counts = [x["total"] for x in top_models]
    m_pcts = [x["pct"] for x in top_models]
    m_colors = [PROVIDER_COLORS[x["provider"]] for x in top_models]

    y_pos = np.arange(len(m_names))
    bars = ax2.barh(y_pos, m_counts, color=m_colors, height=0.6, edgecolor="none", zorder=3)
    for bar, count, pct in zip(bars, m_counts, m_pcts):
        ax2.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2.0,
                 f"{count:,} ({pct:.1f}%)", ha="left", va="center", fontsize=9, fontweight="bold")

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(m_names, fontsize=9.5)
    ax2.invert_yaxis()
    ax2.set_xlabel("Conversations Generated")
    ax2.set_xlim(0, 500)
    ax2.set_title("Top 8 Contributing Generator Models", pad=10, fontsize=12)
    ax2.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig5_domain_model_heatmap(folder_domains, folders_summary, out_path: Path):
    """Plot cross-tabulation heatmap of Domain vs Top Models."""
    significant_folders = [f for f, d in folders_summary.items() if d["total"] >= 50]
    significant_folders.sort(key=lambda f: folders_summary[f]["total"], reverse=True)

    domains_list = [
        "economics_and_finance", "statistics_and_data", "physics", "quantitative_aptitude",
        "chemistry", "logic_and_puzzles", "ethics_and_critical_thinking", "biology",
        "computer_science_light", "history_and_social_studies", "language_and_grammar", "everyday_reasoning"
    ]
    domain_display = [clean_domain_label(d) for d in domains_list]
    model_display = [folders_summary[f]["model_name"] for f in significant_folders]

    matrix = np.zeros((len(domains_list), len(significant_folders)))

    for j, f in enumerate(significant_folders):
        f_total = folders_summary[f]["total"]
        f_doms = folder_domains[f]["domains"]
        for i, d in enumerate(domains_list):
            c = f_doms.get(d, {}).get("count", 0)
            matrix[i, j] = (c / f_total * 100) if f_total > 0 else 0

    fig, ax = plt.subplots(figsize=(15, 8.5), dpi=300)
    
    sns.heatmap(
        matrix,
        annot=True,
        fmt=".1f",
        cmap="YlGnBu",
        cbar_kws={"label": "Domain Share within Model (%)"},
        xticklabels=model_display,
        yticklabels=domain_display,
        linewidths=0.5,
        linecolor="#F1F5F9",
        ax=ax
    )

    ax.set_title("Cross-Tabulation: Domain Distribution Across Top Generator Models (%) \n"
                 "Confirms Consistent Domain Allocation Across Diverse Model Backends",
                 pad=15, fontsize=13, fontweight="bold")
    plt.xticks(rotation=35, ha="right", fontsize=9.5)
    plt.yticks(fontsize=10)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig6_config_stability_by_model(params_data, folders_summary, out_path: Path):
    """Plot stacked 100% bar charts demonstrating uniform parameter sampling across top models."""
    significant_folders = [f for f, d in folders_summary.items() if d["total"] >= 100]
    significant_folders.sort(key=lambda f: folders_summary[f]["total"], reverse=True)
    model_names = [folders_summary[f]["model_name"] for f in significant_folders]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6.5), dpi=300)
    fig.suptitle("Sampling Stability Across Top Models for Core Configuration Parameters",
                 fontsize=15, fontweight="bold", y=0.98)

    configs_to_plot = [
        ("socratic_level", "Socratic Level", ["low", "medium", "high"]),
        ("student_level", "Student Level", ["beginner", "intermediate", "advanced"]),
        ("difficulty", "Problem Difficulty", ["easy", "medium", "hard"])
    ]

    for ax_idx, (pname, ptitle, levels) in enumerate(configs_to_plot):
        ax = axes[ax_idx]
        y_pos = np.arange(len(model_names))
        left_acc = np.zeros(len(model_names))

        for l_idx, level in enumerate(levels):
            pct_vals = []
            for f in significant_folders:
                fdata = params_data[pname]["folders"][f]
                pct = fdata["values"].get(level, {}).get("pct", 0.0)
                pct_vals.append(pct)
            pct_vals = np.array(pct_vals)

            ax.barh(y_pos, pct_vals, left=left_acc, height=0.6,
                    label=level.capitalize(), color=COLORS_3CAT[l_idx], edgecolor="white", zorder=3)
            left_acc += pct_vals

        ax.axvline(33.33, color="black", linestyle=":", linewidth=1, alpha=0.5, zorder=4)
        ax.axvline(66.67, color="black", linestyle=":", linewidth=1, alpha=0.5, zorder=4)

        ax.set_yticks(y_pos)
        if ax_idx == 0:
            ax.set_yticklabels(model_names, fontsize=9.5)
        else:
            ax.set_yticklabels([])
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xlabel("Proportion (%)")
        ax.xaxis.set_major_formatter(ticker.PercentFormatter())
        ax.set_title(ptitle, pad=10, fontsize=12)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.22), ncol=3, frameon=True, fontsize=9.5)
        ax.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)

    plt.tight_layout(rect=[0, 0.05, 1, 0.94])
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_fig7_executive_summary_dashboard(total_convs, params_data, overall_domains, folders_summary, out_path: Path):
    """High-impact master executive infographic summarizing data distribution."""
    fig = plt.figure(figsize=(18, 11), dpi=300)
    fig.patch.set_facecolor("#FFFFFF")
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], width_ratios=[1, 1.2], hspace=0.32, wspace=0.25)

    fig.suptitle("Socratic AI Synthetic Dataset: Generation & Distribution Dashboard\n"
                 f"Comprehensive Analysis of N = {total_convs:,} Multi-Turn Socratic Conversations",
                 fontsize=16, fontweight="bold", y=0.98, color="#0F172A")

    # PANEL A: Provider & Volume Breakdown
    ax_a = fig.add_subplot(gs[0, 0])
    prov_counts = {"Google": 0, "NVIDIA": 0, "OpenRouter": 0}
    for item in folders_summary.values():
        prov_counts[item["provider"]] = prov_counts.get(item["provider"], 0) + item["total"]
    
    prov_labels = [p for p in ["Google", "NVIDIA", "OpenRouter"] if p in prov_counts]
    prov_vals = [prov_counts[p] for p in prov_labels]
    prov_colors = [PROVIDER_COLORS[p] for p in prov_labels]

    wedges, texts, autotexts = ax_a.pie(
        prov_vals,
        labels=[f"{p}\n({prov_counts[p]:,} convs)" for p in prov_labels],
        autopct="%1.1f%%",
        pctdistance=0.72,
        startangle=140,
        colors=prov_colors,
        wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2.5),
        textprops=dict(fontsize=10)
    )
    for at in autotexts:
        at.set_fontsize(10.5)
        at.set_fontweight("bold")
        at.set_color("white")
    ax_a.text(0, 0, f"{total_convs:,}\nTotal\nConvs", ha="center", va="center",
              fontsize=11.5, fontweight="bold", color="#0F172A")
    ax_a.set_title("A. Generation Share by Model Platform", fontsize=12, pad=10)

    # PANEL B: 12 Domain Distribution
    ax_b = fig.add_subplot(gs[0, 1])
    sorted_domains = sorted(overall_domains.items(), key=lambda x: x[1]["count"], reverse=True)
    d_names = [clean_domain_label(k) for k, v in sorted_domains]
    d_counts = [v["count"] for k, v in sorted_domains]
    y_pos = np.arange(len(d_names))
    cmap = sns.color_palette("mako", n_colors=len(d_names))
    bars = ax_b.barh(y_pos, d_counts, color=cmap, height=0.68, edgecolor="none", zorder=3)
    target_count = total_convs / 12.0
    ax_b.axvline(target_count, color="#DC2626", linestyle="--", linewidth=1.2, zorder=4,
                 label=f"Ideal Target ({target_count:.1f})")
    for bar, count in zip(bars, d_counts):
        pct = (count / total_convs) * 100
        ax_b.text(bar.get_width() + 4, bar.get_y() + bar.get_height()/2.0,
                  f"{count} ({pct:.1f}%)", ha="left", va="center", fontsize=8.5, fontweight="bold")
    ax_b.set_yticks(y_pos)
    ax_b.set_yticklabels(d_names, fontsize=8.5)
    ax_b.invert_yaxis()
    ax_b.set_xlim(0, 370)
    ax_b.set_xlabel("Number of Conversations", fontsize=9.5)
    ax_b.set_title("B. Domain Distribution (12 Balanced Academic Fields)", fontsize=12, pad=10)
    ax_b.legend(loc="lower right", frameon=True, fontsize=8.5)
    ax_b.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)

    # PANEL C: 6 Configuration Parameters
    ax_c = fig.add_subplot(gs[1, 0])
    configs = [
        ("socratic_level", "Socratic"),
        ("student_level", "Student Lvl"),
        ("hint_frequency", "Hint Freq"),
        ("mistake_frequency", "Mistake Freq"),
        ("verbosity", "Verbosity"),
        ("difficulty", "Difficulty")
    ]
    x_indices = np.arange(len(configs))
    bar_w = 0.25
    for c_idx, label in enumerate(["Low / Beg / Easy", "Medium / Inter", "High / Adv / Hard"]):
        pct_list = []
        for pname, _ in configs:
            keys = list(params_data[pname]["overall"].keys())
            pct = params_data[pname]["overall"][keys[c_idx]]["pct"]
            pct_list.append(pct)
        ax_c.bar(x_indices + (c_idx - 1)*bar_w, pct_list, width=bar_w,
                 color=COLORS_3CAT[c_idx], label=label, edgecolor="none", zorder=3)
    
    ax_c.axhline(33.33, color="#64748B", linestyle="--", linewidth=1.2, zorder=4, label="Ideal 33.3%")
    ax_c.set_xticks(x_indices)
    ax_c.set_xticklabels([c[1] for c in configs], fontsize=9)
    ax_c.set_ylim(20, 45)
    ax_c.set_ylabel("Parameter Share (%)", fontsize=9.5)
    ax_c.yaxis.set_major_formatter(ticker.PercentFormatter())
    ax_c.set_title("C. 6 Configuration Dimensions (High Sampling Uniformity)", fontsize=12, pad=10)
    ax_c.legend(loc="upper right", frameon=True, fontsize=8, ncol=2)
    ax_c.grid(axis="y", linestyle=":", alpha=0.6, zorder=1)

    # PANEL D: Top 6 Contributing LLMs
    ax_d = fig.add_subplot(gs[1, 1])
    top_models = sorted(folders_summary.values(), key=lambda x: x["total"], reverse=True)[:6]
    tm_names = [x["model_name"] for x in top_models]
    tm_counts = [x["total"] for x in top_models]
    tm_colors = [PROVIDER_COLORS[x["provider"]] for x in top_models]
    y_pos_d = np.arange(len(tm_names))
    bars_d = ax_d.barh(y_pos_d, tm_counts, color=tm_colors, height=0.6, edgecolor="none", zorder=3)
    for bar, count in zip(bars_d, tm_counts):
        pct = (count / total_convs) * 100
        ax_d.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2.0,
                  f"{count:,} ({pct:.1f}%)", ha="left", va="center", fontsize=8.5, fontweight="bold")
    ax_d.set_yticks(y_pos_d)
    ax_d.set_yticklabels(tm_names, fontsize=8.5)
    ax_d.invert_yaxis()
    ax_d.set_xlim(0, 500)
    ax_d.set_xlabel("Conversations Generated", fontsize=9.5)
    ax_d.set_title("D. Top 6 Generator Models by Output Volume", fontsize=12, pad=10)
    ax_d.grid(axis="x", linestyle=":", alpha=0.6, zorder=1)

    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def main():
    base_dir = Path(__file__).resolve().parent.parent
    log_file = base_dir / "data_distribution.log"
    out_dir = base_dir / "reports" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    artifact_env = os.getenv("ARTIFACT_DIR")
    artifact_dir = Path(artifact_env) if artifact_env else None
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading log: {log_file}")
    total_convs, params_data, overall_domains, folder_domains, folders_summary = parse_log_file(log_file)
    print(f"Parsed {total_convs} conversations successfully.")

    figures = [
        ("fig1_config_parameters_balance.png", lambda p: plot_fig1_config_parameters(total_convs, params_data, p)),
        ("fig2_domain_distribution.png", lambda p: plot_fig2_domain_distribution(total_convs, overall_domains, p)),
        ("fig3_model_generator_distribution.png", lambda p: plot_fig3_model_generator_distribution(total_convs, folders_summary, p)),
        ("fig4_provider_and_model_hierarchy.png", lambda p: plot_fig4_provider_and_model_hierarchy(total_convs, folders_summary, p)),
        ("fig5_domain_model_heatmap.png", lambda p: plot_fig5_domain_model_heatmap(folder_domains, folders_summary, p)),
        ("fig6_config_stability_by_model.png", lambda p: plot_fig6_config_stability_by_model(params_data, folders_summary, p)),
        ("fig7_executive_summary_dashboard.png", lambda p: plot_fig7_executive_summary_dashboard(total_convs, params_data, overall_domains, folders_summary, p)),
    ]

    for fname, func in figures:
        target = out_dir / fname
        func(target)
        if artifact_dir:
            artifact_target = artifact_dir / fname
            shutil.copy2(target, artifact_target)
            print(f"Copied to artifact dir: {artifact_target}")

    print("\nAll 7 publication-grade figures successfully generated!")

if __name__ == "__main__":
    main()
