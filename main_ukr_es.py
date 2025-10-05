import argparse, json, os, math, tempfile, random
import numpy as np
from moviepy import (
    ImageClip, AudioFileClip, CompositeAudioClip,
    CompositeVideoClip, ColorClip, VideoClip, concatenate_videoclips
)
from moviepy.video.io.VideoFileClip import VideoFileClip
from PIL import Image, ImageDraw, ImageFont
from edge_tts_helper import tts_edge   # наш помічник TTS

# ---------- TTS wrapper ----------
def tts_edge_multi(text, lang_code, out_path):
    # lang_code: 'uk' | 'es' | ...
    tts_edge(text, lang_code, out_path)

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

# ---------- helpers ----------
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def hex_to_rgb(hx):
    hx = hx.lstrip("#")
    return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))

def rgba_image_to_clip(img_rgba, position, duration):
    """PIL RGBA -> (RGB clip + mask) коректно для прозорості"""
    arr = np.array(img_rgba)
    if arr.ndim == 3 and arr.shape[2] == 4:
        rgb = arr[:, :, :3]
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
    # легке кільце-фон
    draw.ellipse(bbox, outline=(255,255,255,int(255*bg_opacity)), width=thickness)
    if frac > 0:
        end = 360*frac
        r,g,b = color_rgb
        draw.arc(bbox, start=-90, end=-90+end, width=thickness, fill=(r,g,b,255))
    return img

def digit_image(w, h, text, center_y, font_size, color_hex):
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = get_font(font_size)
    r,g,b = hex_to_rgb(color_hex); fill = (r,g,b,255)
    bbox = draw.textbbox((0, 0), text, font=font)
    w_px, h_px = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - w_px)//2
    y = int(center_y - h_px*0.5)
    draw.text((x,y), text, font=font, fill=fill)
    return img

def make_timer_clip(W,H,dur,center,radius,thickness,accent_rgb,bg_opacity, number_center_y, font_size, text_color, show_numbers=False):
    # кільце
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
    ring_clip = ring_clip.with_mask(ring_mask_clip)

    if show_numbers:
        def num_frame(t):
            n = max(0, int(math.ceil(dur - t)))
            img = digit_image(W,H,f"{n}", number_center_y, font_size, text_color)
            return np.array(img)[:, :, :3]
        def num_mask(t):
            n = max(0, int(math.ceil(dur - t)))
            img = digit_image(W,H,f"{n}", number_center_y, font_size, text_color)
            return np.array(img)[:, :, 3].astype(float)/255.0

        num_clip = VideoClip(num_frame).with_duration(dur)
        num_mask_clip = VideoClip(num_mask).with_duration(dur)
        num_clip = num_clip.with_mask(num_mask_clip)
        return CompositeVideoClip([ring_clip, num_clip]).with_duration(dur)

    return ring_clip

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_ukr_es.json")
    ap.add_argument("--dict",   default="inputs_ukr_es/words.json", help="JSON: [{'uk'|'ukr':'...','es':'...'}, ...] або {uk: es, ...}")
    ap.add_argument("--out",    default="outputs/lesson_ukr_es.mp4")
    ap.add_argument("--stage_seconds", type=float, default=4.0, help="таймер (сек)")
    ap.add_argument("--pause_mid", type=float, default=1.2, help="пауза до/після підбадьорення між блоками")
    ap.add_argument("--end_tail", type=float, default=0.5, help="фінальна затримка після фінального спічу (сек)")
    ap.add_argument("--tts_q_lang", default="uk", help="мова питання (укр)")
    ap.add_argument("--tts_a_lang", default="es", help="мова відповіді (ісп)")
    ap.add_argument("--timer_show_numbers", action="store_true", help="показувати цифри у таймері")
    # вступ і підбадьорення
    ap.add_argument("--intro_phrase", default="Ну що ж, час іспанської!", help="фраза на старті")
    ap.add_argument("--intro_lang",   default="uk")
    ap.add_argument("--mid_praise",   default="", help="JSON-файл зі списком підбадьорень після 5-ї; якщо пусто — вбудовані")
    ap.add_argument("--final_praise", default="", help="JSON-файл зі списком фінальних підбадьорень; якщо пусто — вбудовані")
    # звукові ефекти (опційно)
    ap.add_argument("--tick_audio", default="", help="луп тикань (wav/mp3)")
    ap.add_argument("--tick_vol", type=float, default=0.12)
    ap.add_argument("--count", type=int, default=10, help="Скільки пар брати на урок")
    ap.add_argument("--seed", type=int, default=None, help="Сид для відтворюваного рандому (необов'язково)")
    args = ap.parse_args()

    # константи
    MIN_DUR = 0.6        # мінімальна тривалість кроку
    q_lead_pause = 0.35  # пауза після "Як буде іспанською" (разово перед 1-ю парою)
    post_pair_gap = 1.0  # пауза між повними парами
    pre_block_pause = args.pause_mid

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
    number_center_y = int(H*C["timer_top_y"] - C["timer_radius"]*0.15)
    num_font_size = C["timer_font_size"]

    # словник uk/ukr → es
    raw = load_json(args.dict)
    pairs = []
    if isinstance(raw, dict):
        for uk, es in raw.items():
            pairs.append({"uk": str(uk), "es": str(es)})
    elif isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict): 
                continue
            uk_val = it.get("uk", it.get("ukr", None))
            es_val = it.get("es", None)
            if uk_val is not None and es_val is not None:
                pairs.append({"uk": str(uk_val), "es": str(es_val)})

    if len(pairs) < args.count:
        raise ValueError(f"У словнику {len(pairs)} пар, потрібно щонайменше {args.count}")

    random.shuffle(pairs)
    lesson = pairs[:args.count]

    # блоки 2×5
    blocks = [lesson[i:i+5] for i in range(0, len(lesson), 5)]
    blocks = blocks[:2]
    print(">>> lesson len:", len(lesson))
    print(">>> blocks sizes:", [len(b) for b in blocks])

    # підбадьорення
    mid_list = load_json(args.mid_praise) if args.mid_praise else [
        "Непогано!", "Це хороший результат.", "Так тримати!", "Бачу, ти наполегливо йдеш до мети.", "Іспанська не така складна!"
    ]
    fin_list = load_json(args.final_praise) if args.final_praise else [
        "Чудовий урок!", "Так тримати!", "Тобі вдалось дійти до фінішу.", "Такими кроками ми швидко все вивчимо.", "Заходь частіше!"
    ]

    # таймлайн
    clips = []
    audio_tracks = []
    t_cursor = 0.0
    tmpdir = tempfile.mkdtemp(prefix="tts_uk_es_")

    # === INTRO ===
    # короткий візуал старту (аватар на 1.5с)
    intro_vis = 1.5
    clips.append(CompositeVideoClip([
        bg_base.with_duration(intro_vis),
        load_avatar_clip(C, W, H, intro_vis)
    ]).with_duration(intro_vis))
    t_cursor += intro_vis

    # озвучка вступу + показ на його тривалість
    intro_mp3 = os.path.join(tmpdir, "intro.mp3")
    intro_text = getattr(args, "intro_phrase", "Ну що ж, час іспанської!")
    intro_lang = getattr(args, "intro_lang", "uk")
    try:
        tts_edge_multi(intro_text, intro_lang, intro_mp3)
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

    # збільшена пауза після вступу
    pause_after_intro = 1.8
    clips.append(CompositeVideoClip([
        bg_base.with_duration(pause_after_intro),
        load_avatar_clip(C, W, H, pause_after_intro)
    ]).with_duration(pause_after_intro))
    t_cursor += pause_after_intro
    # === /INTRO ===

    # допоміжний рендер блоків (рівно 5 рядків)
    def render_block_layer(duration, left_lines, right_lines):
        n_rows = 5
        left_img  = make_column_image_fixed_rows(left_lines,  n_rows, font_size, col_w, text_color)
        right_img = make_column_image_fixed_rows(right_lines, n_rows, font_size, col_w, text_color)
        left_clip  = rgba_image_to_clip(left_img,  (left_x, cols_top_y), duration)
        right_clip = rgba_image_to_clip(right_img, (right_x, cols_top_y), duration)
        avatar_clip = load_avatar_clip(C, W, H, duration)
        return [bg_base.with_duration(duration), avatar_clip, left_clip, right_clip]

    # робота з двома блоками по 5
    left_accum, right_accum = [], []
    stage = args.stage_seconds

    for bi, block_pairs in enumerate(blocks):
        if len(block_pairs) < 5:
            print(f">>> skip short block {bi} (len={len(block_pairs)})")
            continue

        for i, pr in enumerate(block_pairs):
            uk = pr["uk"].strip()
            es = pr["es"].strip()

            # 1) ЛИШЕ ОДИН РАЗ перед першою парою уроку: “Як буде іспанською”
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

            # 2) УКРАЇНСЬКЕ слово (озвучка + показ зліва)
            uk_path = os.path.join(tmpdir, f"uk_b{bi}_i{i}.mp3")
            try:
                tts_edge_multi(uk, args.tts_q_lang, uk_path)  # укр. голос
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

            # 3) Таймер (кільце без цифр)
            ring = make_timer_clip(
                W, H, stage, timer_center, timer_radius, timer_thickness,
                accent_rgb, timer_bg_opacity, number_center_y, num_font_size, text_color,
                show_numbers=False
            )
            clips.append(CompositeVideoClip(
                render_block_layer(stage, left_accum, right_accum) + [ring]
            ).with_duration(stage))
            t_cursor += stage

            # 4) ІСПАНСЬКЕ слово (озвучка + показ справа)
            a_path = os.path.join(tmpdir, f"a_b{bi}_i{i}.mp3")
            try:
                tts_edge_multi(es, args.tts_a_lang, a_path)
                a_clip = AudioFileClip(a_path).with_start(t_cursor)
                a_dur  = max(MIN_DUR, a_clip.duration or 0.0)
                audio_tracks.append(a_clip)
            except Exception as e:
                print("A TTS error:", e)
                a_dur = MIN_DUR

            right_accum.append(es)
            clips.append(CompositeVideoClip(
                render_block_layer(a_dur, left_accum, right_accum)
            ).with_duration(a_dur))
            t_cursor += a_dur

            # 5) Пауза між парами (1.0с)
            clips.append(CompositeVideoClip(
                render_block_layer(post_pair_gap, left_accum, right_accum)
            ).with_duration(post_pair_gap))
            t_cursor += post_pair_gap

        # --- кінець блоку з 5 пар ---
        if bi == 0:
            # пауза
            clips.append(CompositeVideoClip(
                render_block_layer(pre_block_pause, left_accum, right_accum)
            ).with_duration(pre_block_pause))
            t_cursor += pre_block_pause

            # рандомна мотиваційна озвучка
            praise = random.choice(mid_list)
            p_path = os.path.join(tmpdir, "mid_praise.mp3")
            try:
                tts_edge_multi(praise, args.tts_q_lang, p_path)
                p_clip = AudioFileClip(p_path).with_start(t_cursor)
                p_dur  = max(MIN_DUR, p_clip.duration or 0.0)
                audio_tracks.append(p_clip)
            except Exception as e:
                print("Mid praise TTS error:", e)
                p_dur = MIN_DUR

            clips.append(CompositeVideoClip(
                render_block_layer(p_dur, left_accum, right_accum)
            ).with_duration(p_dur))
            t_cursor += p_dur

            # пауза і очищення перед другим блоком
            clips.append(CompositeVideoClip(
                render_block_layer(pre_block_pause, left_accum, right_accum)
            ).with_duration(pre_block_pause))
            t_cursor += pre_block_pause

            left_accum, right_accum = [], []
            clear_dur = 0.2
            clips.append(CompositeVideoClip(
                render_block_layer(clear_dur, left_accum, right_accum)
            ).with_duration(clear_dur))
            t_cursor += clear_dur

    # фінальна пауза + фінальне підбадьорення
    clips.append(CompositeVideoClip(
        render_block_layer(pre_block_pause, left_accum, right_accum)
    ).with_duration(pre_block_pause))
    t_cursor += pre_block_pause

    final_phrase = random.choice(fin_list)
    f_path = os.path.join(tmpdir, "final_praise.mp3")
    try:
        tts_edge_multi(final_phrase, args.tts_q_lang, f_path)
        f_clip = AudioFileClip(f_path).with_start(t_cursor)
        f_dur  = max(MIN_DUR, f_clip.duration or 0.0)
        audio_tracks.append(f_clip)
    except Exception as e:
        print("Final praise TTS error:", e)
        f_dur = MIN_DUR

    clips.append(CompositeVideoClip(
        render_block_layer(f_dur, left_accum, right_accum)
    ).with_duration(f_dur))
    t_cursor += f_dur

    # хвостик
    end_tail = args.end_tail
    if end_tail > 0:
        clips.append(CompositeVideoClip(
            render_block_layer(end_tail, left_accum, right_accum)
        ).with_duration(end_tail))
        t_cursor += end_tail

    # аудіо-трек тикань (опц.) на весь ролик
    final_tracks = list(audio_tracks)
    video = concatenate_videoclips(clips, method="compose")
    if args.tick_audio:
        try:
            tick = AudioFileClip(args.tick_audio).volumex(args.tick_vol)
            pos = 0.0
            ticks = []
            while pos < video.duration:
                ticks.append(tick.with_start(pos))
                pos += max(0.1, tick.duration)
            final_tracks.extend(ticks)
        except Exception as e:
            print("Tick audio error:", e)

    if final_tracks:
        final_audio = CompositeAudioClip(final_tracks).with_duration(video.duration)
        video = video.with_audio(final_audio)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    video.write_videofile(args.out, fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4)

if __name__ == "__main__":
    print(">>> UKR->ES single-block flow, 2x5 pairs, Q->timer->A with praises")
    main()
