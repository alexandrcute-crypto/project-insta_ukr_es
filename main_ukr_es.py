import argparse, json, os, math, subprocess, tempfile, random
import numpy as np
from moviepy import (
    ImageClip, AudioFileClip, CompositeAudioClip,
    CompositeVideoClip, ColorClip, VideoClip, concatenate_videoclips
)
from moviepy.video.io.VideoFileClip import VideoFileClip
from PIL import Image, ImageDraw, ImageFont
from edge_tts_helper import tts_edge   # твій помічник TTS (edge-tts)

# ---------- TTS wrapper ----------
def tts_edge_multi(text, lang_code, out_path):
    # lang_code: 'uk' | 'es' | ...
    tts_edge(text, lang_code, out_path)

# ---------- ffmpeg helpers (гучність/швидкість для іспанської озвучки) ----------
def ffmpeg_tempo_volume(in_path, out_path, tempo=1.0, volume=1.0):
    """
    Змінює швидкість (tempo) та гучність (volume) аудіо через ffmpeg.
    tempo  <1.0 → повільніше; >1.0 → швидше. Діапазон atempo: 0.5..2.0
    volume >1.0 → голосніше;  <1.0 → тихіше.
    """
    # приклад фільтра: atempo=0.95,volume=1.05
    filt = f"atempo={tempo},volume={volume}"
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-af", filt, out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------- fonts ----------
def get_font(font_size: int):
    candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, font_size)
            except Exception:
                pass
    return ImageFont.load_default()

# ---------- utils ----------
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def hex_to_rgb(hx):
    hx = hx.lstrip("#")
    return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))

def rgba_image_to_clip(img_rgba, position, duration):
    """PIL RGBA -> (RGB clip + маска прозорості), сумісно з MoviePy v2.x."""
    arr = np.array(img_rgba)
    if arr.ndim == 3 and arr.shape[2] == 4:
        rgb   = arr[:, :, :3]
        alpha = (arr[:, :, 3].astype(float) / 255.0)
        base = ImageClip(rgb).with_position(position).with_duration(duration)
        mask = ImageClip(alpha).with_position(position).with_duration(duration).with_mask()
        return base.with_mask(mask)
    else:
        return ImageClip(arr).with_position(position).with_duration(duration)

def make_column_image_fixed_rows(lines, n_rows, font_size, width_px, color_hex, line_spacing=1.25, pad_y=10):
    font = get_font(font_size)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    row_h = int(line_h * line_spacing)
    total_h = n_rows * row_h + 2 * pad_y

    img = Image.new("RGBA", (width_px, total_h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    r,g,b = hex_to_rgb(color_hex); fill = (r,g,b,255)

    for i in range(n_rows):
        txt = lines[i] if i < len(lines) else ""
        bbox = draw.textbbox((0, 0), txt, font=font)
        w_px, h_px = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (width_px - w_px)//2
        y = pad_y + i*row_h + (row_h - h_px)//2
        draw.text((x,y), txt, font=font, fill=fill)
    return img

def ring_image(w, h, frac, center, radius, thickness, color_rgb, bg_opacity=0.3):
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    cx, cy = center
    bbox = [cx-radius, cy-radius, cx+radius, cy+radius]
    draw.ellipse(bbox, outline=(255,255,255,int(255*bg_opacity)), width=thickness)
    if frac > 0:
        end = 360*frac
        r,g,b = color_rgb
        draw.arc(bbox, start=-90, end=-90+end, width=thickness, fill=(r,g,b,255))
    return img

def make_timer_clip(W,H,dur,center,radius,thickness,accent_rgb,bg_opacity):
    # тільки кільце без цифр
    def ring_frame(t):
        frac = min(1.0, max(0.0, t/dur))
        img = ring_image(W,H,frac,center,radius,thickness,accent_rgb,bg_opacity)
        return np.array(img)[:, :, :3]
    def ring_mask(t):
        frac = min(1.0, max(0.0, t/dur))
        img = ring_image(W,H,frac,center,radius,thickness,accent_rgb,bg_opacity)
        return np.array(img)[:, :, 3].astype(float)/255.0

    ring_clip = VideoClip(ring_frame).with_duration(dur)
    ring_mask_clip = VideoClip(ring_mask).with_duration(dur)
    return ring_clip.with_mask(ring_mask_clip)

def load_avatar_clip(C, W, H, duration):
    avatar_path = C["avatar"]
    ext = os.path.splitext(avatar_path)[1].lower()
    if ext in [".gif", ".mp4", ".mov", ".webm"]:
        v = VideoFileClip(avatar_path).loop(duration=duration).with_duration(duration)
        target_w = int(W * C["avatar_scale"])
        v = v.resize(width=target_w)
        return v.with_position(("center", int(H*C["avatar_y"] - v.h/2)))
    else:
        img = Image.open(avatar_path).convert("RGBA")
        aw = int(W*C["avatar_scale"]); ah = int(img.height * (aw/img.width))
        img = img.resize((aw,ah), resample=Image.LANCZOS)
        pos = ("center", int(H*C["avatar_y"] - ah/2))
        return rgba_image_to_clip(img, pos, duration)

# ---------- main ----------
def main():
    # базові константи для синхронізації
    MIN_DUR = 0.6          # анти-нуль
    q_lead_pause = 0.35    # пауза після "Як буде іспанською" (перед 1-ю парою)
    post_pair_gap = 1.0    # пауза між повними парами
    pre_block_pause = 1.2  # пауза перед/після підбадьорення

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_ukr_es.json")
    ap.add_argument("--dict",   default="inputs_ukr_es/words.json")
    ap.add_argument("--out",    default="outputs/lesson_ukr_es.mp4")
    ap.add_argument("--stage_seconds", type=float, default=4.0, help="таймер (сек)")
    ap.add_argument("--tts_q_lang", default="uk", help="мова питання та укр-слова")
    ap.add_argument("--tts_a_lang", default="es", help="мова відповіді (ісп)")
    ap.add_argument("--intro_phrase", default="Ну що ж, час іспанської!")
    ap.add_argument("--intro_lang",   default="uk")
    ap.add_argument("--count", type=int, default=10, help="скільки пар брати (очікуємо 10 для 2×5)")
    ap.add_argument("--seed", type=int, default=None)
    # налаштування для іспанської озвучки
    ap.add_argument("--es_tempo", type=float, default=0.95, help="швидкість (atempo) іспанської, 0.95=на 5% повільніше")
    ap.add_argument("--es_gain",  type=float, default=1.05, help="гучність іспанської, 1.05=+5%")
    # цільова тривалість (паддинг)
    ap.add_argument("--target_len", type=float, default=0.0, help="якщо >0, допадити ролик до цієї тривалості (сек)")

    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    C = load_json(args.config)
    W,H = C["width"], C["height"]; FPS = C["fps"]

    # фон
    if "bg_image" in C and C["bg_image"]:
        bg_base = ImageClip(C["bg_image"]).resize((W,H)).with_duration(0.1)
    else:
        bg_base = ColorClip(size=(W,H), color=hex_to_rgb(C["bg_color"])).with_duration(0.1)

    # геометрія
    cols_top_y = int(H*C["cols_top_y"])
    col_w = int(W*C["col_width"]); margin = int(W*C["col_margin"])
    left_x = margin; right_x = W - margin - col_w
    font_size = int(C.get("font_size",56)); text_color = C.get("text_color","#ffffff")

    # таймер
    accent_rgb = hex_to_rgb(C["accent_color"])
    timer_center = (W//2, int(H*C["timer_top_y"]))
    timer_radius = C["timer_radius"]; timer_thickness = C["timer_thickness"]
    timer_bg_opacity = C["timer_bg_opacity"]

    # словник uk→es
    raw = load_json(args.dict)
    pairs = []
    if isinstance(raw, dict):
        for uk, es in raw.items():
            pairs.append({"uk": str(uk), "es": str(es)})
    elif isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict) and "ukr" in it and "es" in it:
                # підтримка ключа "ukr" з твоїх файлів
                pairs.append({"uk": str(it["ukr"]), "es": str(it["es"])})
            elif isinstance(it, dict) and "uk" in it and "es" in it:
                pairs.append({"uk": str(it["uk"]), "es": str(it["es"])})
    random.shuffle(pairs)
    if len(pairs) < args.count:
        raise ValueError(f"У словнику {len(pairs)} пар, потрібно щонайменше {args.count}")
    lesson = pairs[:args.count]

    # рівно 2×5
    blocks = [lesson[i:i+5] for i in range(0, len(lesson), 5)][:2]

    # підбадьорення за замовчуванням
    mid_list = [
        "Непогано!", "Це хороший результат.", "Так тримати!",
        "Бачу, ти наполегливо йдеш до мети.", "Іспанська не така складна!"
    ]
    fin_list = [
        "Чудовий урок!", "Так тримати!", "Тобі вдалось дійти до фінішу.",
        "Такими кроками ми швидко все вивчимо.", "Заходь частіше!"
    ]

    clips = []
    audio_tracks = []
    t_cursor = 0.0
    tmpdir = tempfile.mkdtemp(prefix="tts_uk_es_")

    # ВСТУП: показ аватара під час озвучки + пауза після
    # (1) тиша з аватаром перед стартом (красиво з'являється)
    pre_intro = 0.6
    clips.append(CompositeVideoClip([
        bg_base.with_duration(pre_intro),
        load_avatar_clip(C, W, H, pre_intro)
    ]).with_duration(pre_intro))
    t_cursor += pre_intro

    # (2) озвучка вступу
    intro_mp3 = os.path.join(tmpdir, "intro.mp3")
    try:
        tts_edge_multi(args.intro_phrase, args.intro_lang, intro_mp3)
        intro_clip = AudioFileClip(intro_mp3).with_start(t_cursor)
        intro_dur = max(MIN_DUR, intro_clip.duration or 1.2)
        audio_tracks.append(intro_clip)
    except Exception as e:
        print("Intro TTS error:", e)
        intro_dur = 1.2

    clips.append(CompositeVideoClip([
        bg_base.with_duration(intro_dur),
        load_avatar_clip(C, W, H, intro_dur)
    ]).with_duration(intro_dur))
    t_cursor += intro_dur

    # (3) пауза після вступу (щоб не злипалось із «Як буде…»)
    post_intro_pause = 1.2
    clips.append(CompositeVideoClip([
        bg_base.with_duration(post_intro_pause),
        load_avatar_clip(C, W, H, post_intro_pause)
    ]).with_duration(post_intro_pause))
    t_cursor += post_intro_pause

    # підготовка відмалювання
    def render_block_layer(duration, left_lines, right_lines):
        n_rows = 5
        left_img  = make_column_image_fixed_rows(left_lines,  n_rows, font_size, col_w, text_color)
        right_img = make_column_image_fixed_rows(right_lines, n_rows, font_size, col_w, text_color)
        left_clip  = rgba_image_to_clip(left_img,  (left_x, cols_top_y), duration)
        right_clip = rgba_image_to_clip(right_img, (right_x, cols_top_y), duration)
        avatar_clip = load_avatar_clip(C, W, H, duration)
        return [bg_base.with_duration(duration), avatar_clip, left_clip, right_clip]

    stage = args.stage_seconds

    left_accum, right_accum = [], []

    for bi, block_pairs in enumerate(blocks):
        if len(block_pairs) < 5:
            continue

        for i, pr in enumerate(block_pairs):
            uk = pr["uk"].strip()
            es = pr["es"].strip()

            # Разова фраза "Як буде іспанською" перед першою парою всього уроку
            if bi == 0 and i == 0:
                q_path = os.path.join(tmpdir, f"q_b{bi}_i{i}.mp3")
                try:
                    tts_edge_multi("Як буде іспанською", args.tts_q_lang, q_path)
                    q_clip = AudioFileClip(q_path).with_start(t_cursor)
                    q_dur  = max(MIN_DUR, q_clip.duration or 0.0)
                    audio_tracks.append(q_clip)
                except Exception as e:
                    print("Q TTS error:", e)
                    q_dur = q_lead_pause

                clips.append(CompositeVideoClip(
                    render_block_layer(q_dur, left_accum, right_accum)
                ).with_duration(q_dur))
                t_cursor += q_dur

                clips.append(CompositeVideoClip(
                    render_block_layer(q_lead_pause, left_accum, right_accum)
                ).with_duration(q_lead_pause))
                t_cursor += q_lead_pause

            # УКРАЇНСЬКЕ слово: озвучити + показати зліва
            uk_path = os.path.join(tmpdir, f"uk_b{bi}_i{i}.mp3")
            try:
                tts_edge_multi(uk, args.tts_q_lang, uk_path)
                uk_clip = AudioFileClip(uk_path).with_start(t_cursor)
                uk_dur  = max(MIN_DUR, uk_clip.duration or 0.0)
                audio_tracks.append(uk_clip)
            except Exception as e:
                print("UK TTS error:", e)
                uk_dur = MIN_DUR

            left_accum.append(uk)
            clips.append(CompositeVideoClip(
                render_block_layer(uk_dur, left_accum, right_accum)
            ).with_duration(uk_dur))
            t_cursor += uk_dur

            # ТАЙМЕР 4с (або args.stage_seconds)
            ring = make_timer_clip(W,H,stage, timer_center,timer_radius,timer_thickness,accent_rgb,timer_bg_opacity)
            clips.append(CompositeVideoClip(
                render_block_layer(stage, left_accum, right_accum) + [ring]
            ).with_duration(stage))
            t_cursor += stage

            # ВІДПОВІДЬ (іспанське слово): сповільнити та підсилити через ffmpeg
            a_in  = os.path.join(tmpdir, f"a_in_b{bi}_i{i}.mp3")
            a_out = os.path.join(tmpdir, f"a_out_b{bi}_i{i}.mp3")
            try:
                tts_edge_multi(es, args.tts_a_lang, a_in)
                ffmpeg_tempo_volume(a_in, a_out, tempo=args.es_tempo, volume=args.es_gain)
                a_clip = AudioFileClip(a_out).with_start(t_cursor)
                a_dur = max(MIN_DUR, a_clip.duration or 0.0)
                audio_tracks.append(a_clip)
            except Exception as e:
                print("A TTS error:", e)
                a_dur = MIN_DUR

            right_accum.append(es)
            clips.append(CompositeVideoClip(
                render_block_layer(a_dur, left_accum, right_accum)
            ).with_duration(a_dur))
            t_cursor += a_dur

            # пауза між парами
            clips.append(CompositeVideoClip(
                render_block_layer(post_pair_gap, left_accum, right_accum)
            ).with_duration(post_pair_gap))
            t_cursor += post_pair_gap

        # Після першого блоку: пауза + підбадьорення + очистка
        if bi == 0:
            clips.append(CompositeVideoClip(
                render_block_layer(pre_block_pause, left_accum, right_accum)
            ).with_duration(pre_block_pause))
            t_cursor += pre_block_pause

            mid_phrase = random.choice(mid_list)
            p_path = os.path.join(tmpdir, "mid_praise.mp3")
            try:
                tts_edge_multi(mid_phrase, args.tts_q_lang, p_path)
                p_clip = AudioFileClip(p_path).with_start(t_cursor)
                p_dur = max(MIN_DUR, p_clip.duration or 0.0)
                audio_tracks.append(p_clip)
            except Exception as e:
                print("Mid praise TTS error:", e); p_dur = MIN_DUR

            clips.append(CompositeVideoClip(
                render_block_layer(p_dur, left_accum, right_accum)
            ).with_duration(p_dur))
            t_cursor += p_dur

            clips.append(CompositeVideoClip(
                render_block_layer(pre_block_pause, left_accum, right_accum)
            ).with_duration(pre_block_pause))
            t_cursor += pre_block_pause

            # Очистити екран і стеки перед другим блоком
            left_accum, right_accum = [], []
            clear_dur = 0.25
            clips.append(CompositeVideoClip(
                render_block_layer(clear_dur, left_accum, right_accum)
            ).with_duration(clear_dur))
            t_cursor += clear_dur

    # Фінал: пауза + фінальна фраза + хвостик
    clips.append(CompositeVideoClip(
        render_block_layer(pre_block_pause, left_accum, right_accum)
    ).with_duration(pre_block_pause))
    t_cursor += pre_block_pause

    final_phrase = random.choice(fin_list)
    f_path = os.path.join(tmpdir, "final_praise.mp3")
    try:
        tts_edge_multi(final_phrase, args.tts_q_lang, f_path)
        f_clip = AudioFileClip(f_path).with_start(t_cursor)
        f_dur = max(MIN_DUR, f_clip.duration or 0.0)
        audio_tracks.append(f_clip)
    except Exception as e:
        print("Final praise TTS error:", e); f_dur = MIN_DUR

    clips.append(CompositeVideoClip(
        render_block_layer(f_dur, left_accum, right_accum)
    ).with_duration(f_dur))
    t_cursor += f_dur

    end_tail = 0.5
    clips.append(CompositeVideoClip(
        render_block_layer(end_tail, left_accum, right_accum)
    ).with_duration(end_tail))
    t_cursor += end_tail

    # Збірка відео + аудіо
    video = concatenate_videoclips(clips, method="compose")
    if audio_tracks:
        final_audio = CompositeAudioClip(audio_tracks).with_duration(video.duration)
        video = video.with_audio(final_audio)

    # Якщо задано target_len — допадити до цієї тривалості (не масштабуємо, а додаємо фінальний статичний кадр)
    if args.target_len and args.target_len > 0:
        cur = video.duration or 0.0
        if cur < args.target_len:
            pad = args.target_len - cur
            pad_layer = CompositeVideoClip(render_block_layer(pad, left_accum, right_accum)).with_duration(pad)
            video = concatenate_videoclips([video, pad_layer], method="compose")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    print(">>> Building video:", args.out)
    video.write_videofile(args.out, fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4)

if __name__ == "__main__":
    print(">>> UKR->ES flow, 2x5 pairs, strict sequencing, ES audio tempo+gain via ffmpeg")
    main()
