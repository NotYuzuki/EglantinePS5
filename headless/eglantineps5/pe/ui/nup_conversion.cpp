// SPDX-License-Identifier: GPL-3.0-or-later
#include "pe/ui/launcher.hpp"
#include "pe/core/strings.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>

namespace pe::ui {
namespace {
std::string nup_size(std::uint64_t bytes) {
    char text[64];
    if (bytes >= (1ULL << 30)) std::snprintf(text, sizeof(text), "%.1f GiB", double(bytes) / (1ULL << 30));
    else std::snprintf(text, sizeof(text), "%.1f MiB", double(bytes) / (1ULL << 20));
    return text;
}
std::string nup_eta(double seconds) {
    if (!std::isfinite(seconds) || seconds < 0 || seconds > 360000) return "--:--";
    const auto value = static_cast<unsigned>(seconds);
    char text[64];
    if (value >= 3600) std::snprintf(text, sizeof(text), "%u:%02u:%02u", value / 3600, value / 60 % 60, value % 60);
    else std::snprintf(text, sizeof(text), "%u:%02u", value / 60, value % 60);
    return text;
}
}

void Launcher::open_nup(bool compressed) {
    if (games_.empty()) return;
    nup_game_ = games_[static_cast<std::size_t>(library_.selected)];
    if (!services_.can_convert_nup(nup_game_.file)) return;
    nup_stop_ = false; nup_completed_ = false; nup_picker_ = false; nup_compressed_ = compressed;
    nup_icon_path_.clear(); nup_icon_preview_.clear(); nup_result_.clear(); message_.clear();
    nup_info_ = {}; nup_option_ = 2; nup_done_ = 0; nup_total_ = 0; nup_files_ = 0; nup_files_total_ = 0;
    nup_validating_ = false; nup_speed_ = 0;
    const auto file = nup_game_.file;
    nup_info_request_ = std::async(std::launch::async, [this, file] {
        try { return services_.nup_info(file, nup_stop_); }
        catch (const std::exception& e) { NupInfo info; info.error = e.what(); return info; }
    });
    modal_ = modal_shown_ = Modal::nup;
    cue(audio::Cue::modal_open);
}

void Launcher::browse_nup_icons(const std::string& path) {
    nup_picker_ = true; nup_icon_directory_ = path; nup_result_.clear();
    nup_icons_request_ = std::async(std::launch::async, [this, path] {
        try { return services_.nup_icons(path); }
        catch (...) { return std::vector<NupIconEntry>{}; }
    });
}

void Launcher::press_nup(Key key) {
    if (nup_info_request_.valid() || nup_conversion_.valid()) {
        if (key == Key::circle) nup_stop_ = true;
        return;
    }
    if (nup_icons_request_.valid() || nup_icon_request_.valid()) return;
    if (nup_picker_) {
        if (key == Key::circle) { nup_picker_ = false; return; }
        if (key == Key::up || key == Key::down) { nup_icon_rows_.move(key == Key::down ? 1 : -1); return; }
        if (key != Key::cross || nup_icons_.empty()) return;
        const auto entry = nup_icons_[static_cast<std::size_t>(nup_icon_rows_.selected)];
        if (entry.directory) { browse_nup_icons(entry.path); return; }
        if (entry.path.empty()) { nup_icon_path_.clear(); nup_icon_preview_.clear(); nup_result_.clear(); nup_picker_ = false; return; }
        nup_icon_request_ = std::async(std::launch::async, [this, path = entry.path] {
            try { return services_.nup_icon(path); }
            catch (const std::exception& e) { NupIconSelection icon; icon.error = e.what(); return icon; }
        });
        return;
    }
    if (key == Key::circle || (nup_completed_ && key == Key::cross)) {
        close_modal();
        if (nup_completed_) start_scan();
        return;
    }
    if (nup_completed_) return;
    if (key == Key::up || key == Key::down) { nup_option_ = std::clamp(nup_option_ + (key == Key::down ? 1 : -1), 0, 2); return; }
    if (key != Key::cross && key != Key::left && key != Key::right) return;
    if (nup_option_ == 0) { nup_compressed_ = !nup_compressed_; return; }
    if (key != Key::cross) return;
    if (nup_option_ == 1) { browse_nup_icons(nup_info_.source); return; }
    if (!nup_info_.error.empty() || nup_info_.source.empty()) return;
    nup_stop_ = false; nup_validating_ = false; nup_done_ = 0; nup_total_ = 0; nup_files_ = 0; nup_files_total_ = 0;
    nup_result_.clear(); nup_speed_ = 0; nup_sample_bytes_ = 0; nup_sample_verifying_ = false;
    nup_sample_clock_ = std::chrono::steady_clock::now();
    nup_conversion_ = std::async(std::launch::async, [this, file = nup_game_.file, compressed = nup_compressed_, icon = nup_icon_path_] {
        try {
            return services_.convert_nup(file, compressed, icon, nup_stop_, [this](const NupProgress& progress) {
                nup_done_ = progress.bytes; nup_total_ = progress.total;
                nup_files_ = progress.files; nup_files_total_ = progress.files_total;
                nup_validating_ = progress.verifying;
            });
        } catch (const std::exception& e) { return std::string("Conversion failed: ") + e.what(); }
    });
}

void Launcher::poll_nup_conversion() {
    using namespace std::chrono;
    if (nup_info_request_.valid() && nup_info_request_.wait_for(seconds(0)) == std::future_status::ready)
        nup_info_ = nup_info_request_.get();
    if (nup_icons_request_.valid() && nup_icons_request_.wait_for(seconds(0)) == std::future_status::ready) {
        nup_icons_ = nup_icons_request_.get();
        nup_icon_rows_.visible = 6; nup_icon_rows_.pitch = 60; nup_icon_rows_.reset(static_cast<int>(nup_icons_.size()), 0);
    }
    if (nup_icon_request_.valid() && nup_icon_request_.wait_for(seconds(0)) == std::future_status::ready) {
        const auto icon = nup_icon_request_.get();
        if (icon.error.empty()) { nup_icon_path_ = icon.path; nup_icon_preview_ = icon.preview; nup_result_.clear(); nup_picker_ = false; }
        else nup_result_ = icon.error;
    }
    if (!nup_conversion_.valid()) return;
    if (nup_conversion_.wait_for(seconds(0)) == std::future_status::ready) {
        nup_result_ = nup_conversion_.get(); nup_completed_ = true;
        cue(nup_result_.starts_with("NUP conversion verified.") ? audio::Cue::saved : audio::Cue::notify);
        return;
    }
    const auto now = steady_clock::now();
    const auto bytes = nup_done_.load(); const bool verifying = nup_validating_.load();
    if (verifying != nup_sample_verifying_ || bytes < nup_sample_bytes_) {
        nup_sample_verifying_ = verifying; nup_sample_bytes_ = bytes; nup_sample_clock_ = now; nup_speed_ = 0;
    } else if (const double elapsed = duration<double>(now - nup_sample_clock_).count(); bytes > nup_sample_bytes_ && elapsed >= 0.5) {
        const double rate = double(bytes - nup_sample_bytes_) / elapsed;
        nup_speed_ = nup_speed_ > 0 ? nup_speed_ * 0.65 + rate * 0.35 : rate;
        nup_sample_bytes_ = bytes; nup_sample_clock_ = now;
    }
}

void Launcher::draw_nup(Canvas& c, float open) {
    auto& list = c.list;
    list.push_opacity(open);
    glass(c, {260, 112, 1400, 850}, 26, theme::kPanel.with_alpha(0.98f), theme::kPanelEdge, 1.6f);
    text_shrink(c, tr(nup_picker_ ? "Conversion icon" : "Convert to NUP"), 320, 190,
                theme::kDisplay, theme::kTitle, 1280);
    cover(c, nup_icon_preview_.empty() ? nup_game_.cover : nup_icon_preview_, {320, 220, 100, 100}, 12);
    text_shrink(c, nup_game_.name, 450, 258, theme::kText24, theme::kValue, 1150);
    text_shrink(c, tr("The source folder is kept. Existing archives are never overwritten."), 450, 300,
                theme::kSmall, theme::kMeta, 1150);
    if (nup_picker_) {
        text_shrink(c, nup_icon_directory_, 320, 356, theme::kSmall, theme::kMeta, 1280);
        nup_icon_rows_.update(1.0f / 60.0f);
        list.push_clip({320,374,1280,366});
        for (int row = nup_icon_rows_.first_row(); row <= nup_icon_rows_.last_row(); ++row) {
            const float y = 380 + row * 60 - nup_icon_rows_.scroll();
            const auto& entry = nup_icons_[static_cast<std::size_t>(row)];
            const bool selected = row == nup_icon_rows_.selected;
            if (selected) list.rounded_rect({320,y,1280,54},8,theme::kPanelEdge.with_alpha(0.5f));
            text_shrink(c, (entry.directory ? "[Folder] " : "") + entry.name, 344, y + 36,
                        theme::kText24, selected ? theme::kLimePale : theme::kValue, 1220);
        }
        list.pop_clip();
        scrollbar(c,nup_icon_rows_,1610,380,360);
        text_shrink(c, nup_icons_request_.valid() || nup_icon_request_.valid() ? tr("Reading icons...") :
                    tr("PNG or JPEG, up to 8 MiB. Cross: choose. Circle: back."), 320, 830,
                    theme::kSmall, theme::kMeta, 1280);
        if (!nup_result_.empty()) text_shrink(c,nup_result_,320,890,theme::kSmall,theme::kWarning,1280);
        list.pop_opacity(); return;
    }
    text_shrink(c, tr("Source"), 320, 356, theme::kSmall, theme::kMeta, 1280);
    text_shrink(c, nup_info_.source.empty() ? services_.game_path(nup_game_.file) : nup_info_.source,
                320, 390, theme::kText24, theme::kValue, 1280);
    text_shrink(c, tr("Target"), 320, 432, theme::kSmall, theme::kMeta, 1280);
    text_shrink(c, nup_info_.target, 320, 466, theme::kText24, theme::kValue, 1280);
    const std::string estimate = nup_info_request_.valid() ? tr("Scanning source files...") :
        tr("Approximate space needed: up to ") + nup_size(nup_info_.bytes + (16ULL<<20)) + "   |   " +
        tr("Free space: ") + (nup_info_.available_known ? nup_size(nup_info_.available) : tr("unavailable"));
    text_shrink(c, estimate, 320, 512, theme::kSmall, theme::kMeta, 1280);
    const bool running = nup_conversion_.valid();
    const char* labels[] = {"Output", "Icon", nup_completed_ ? "Finished" : running ? "Conversion" : "Start conversion"};
    const std::string values[] = {tr(nup_compressed_ ? "Fast compression" : "Raw (fastest)"),
        nup_icon_path_.empty() ? tr("Detected icon") : std::filesystem::path(nup_icon_path_).filename().string(),
        tr(nup_completed_ ? "Close" : running ? "Working..." : "Cross to start")};
    for (int row = 0; row < 3; ++row) {
        const float y = 542 + row * 60;
        const bool focus = row == nup_option_ && !running;
        list.rounded_rect({320,y,1280,54},8,theme::kPanelEdge.with_alpha(focus ? 0.5f : 0.15f));
        text_shrink(c,tr(labels[row]),344,y+36,theme::kText24,theme::kValue,400);
        text_shrink(c,values[row],1576,y+36,theme::kText24,focus ? theme::kLimePale : theme::kMeta,780,Align::right);
    }
    if (running) {
        const auto total=nup_total_.load(), done=nup_done_.load();
        const float fraction=total ? static_cast<float>(std::clamp(double(done)/total,0.0,1.0)) : 0;
        const bool verifying=nup_validating_.load();
        const std::string phase=nup_stop_ ? tr("Cancelling...") : verifying ? tr("Verifying NUP") : total ? tr("Converting to NUP") : tr("Preparing conversion...");
        text_shrink(c,phase,320,754,theme::kText24,theme::kValue,1000);
        text_shrink(c,std::to_string(static_cast<unsigned>(fraction*100))+"%",1600,754,theme::kText24,theme::kLimePale,180,Align::right);
        list.rounded_rect({320,775,1280,10},5,theme::kPanelEdge.with_alpha(0.3f));
        if (fraction>0) list.rounded_rect({320,775,std::max(10.0f,1280*fraction),10},5,theme::kLime);
        text_shrink(c,nup_size(done)+" / "+nup_size(total ? total : nup_info_.bytes),320,824,theme::kText24,theme::kValue,600);
        text_shrink(c,std::to_string(nup_files_.load())+" / "+std::to_string(nup_files_total_.load() ? nup_files_total_.load() : nup_info_.files)+tr(" files"),1600,824,theme::kText24,theme::kValue,620,Align::right);
        char rate[64]; std::snprintf(rate,sizeof(rate),"%.1f MiB/s",nup_speed_/(1ULL<<20));
        const auto remaining = total>done ? total-done : 0;
        const bool waiting = std::chrono::duration<double>(std::chrono::steady_clock::now() - nup_sample_clock_).count() > 5;
        const std::string timing=waiting ? tr("Processing the current file...") : nup_speed_>1 ? std::string(rate)+"   |   "+tr("About ")+nup_eta(double(remaining)/nup_speed_)+tr(" left") : tr("Estimating speed and time remaining...");
        text_shrink(c,timing,320,868,theme::kSmall,theme::kMeta,1280);
        text_shrink(c,tr("Circle: cancel"),320,918,theme::kSmall,theme::kMeta,1280);
    } else {
        const auto status=!nup_result_.empty() ? nup_result_ : nup_info_.error;
        if (!status.empty()) notice_block(c,status,320,766,theme::kText24,32,
            nup_result_.starts_with("NUP conversion verified.") ? theme::kLimePale : theme::kWarning,1280,2,!nup_result_.starts_with("NUP conversion verified."));
        text_shrink(c,tr("Up/Down: select. Cross: open or start. Left/Right: change output. Circle: back."),
                    320,918,theme::kSmall,theme::kMeta,1280);
    }
    list.pop_opacity();
}
} // namespace pe::ui
