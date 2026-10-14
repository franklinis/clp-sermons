from datetime import date, datetime, time
import json

from sermon_clips import picker, posts, review
from sermon_clips.transcript import Transcript
from sermon_clips.youtube import Sermon


def make_transcript(tmp_path, words):
    """json3 with one event per word: [(seconds, word), ...]."""
    events = [{"tStartMs": int(t * 1000), "dDurationMs": 500, "segs": [{"utf8": w}]} for t, w in words]
    path = tmp_path / "c.json3"
    path.write_text(json.dumps({"events": events}), encoding="utf-8")
    return Transcript.load(path)


WORDS = [(10.0, "ஆனா"), (10.4, "உண்மை"), (10.9, "என்னன்னா,"), (11.5, "எதுவுமே"), (12.0, "free"),
         (12.4, "கிடையாது."), (14.0, "நான்"), (14.3, "ஒரு"), (14.6, "ஊழிய"), (15.2, "ஸ்தாபனத்துல"),
         (16.0, "வேலை"), (16.5, "செய்யறேன்."), (20.0, "அடுத்த"), (20.5, "விஷயம்")]


def test_cut_points_follow_the_quoted_words(tmp_path):
    t = make_transcript(tmp_path, WORDS)
    # Claude gives the caption line's time, which can be a little before the words.
    assert abs(t.cut_start(9.0, "ஆனா உண்மை என்னன்னா") - 9.88) < 0.01
    # The clip ends just before the next word, so the last word is never clipped...
    assert abs(t.cut_end(11.0, "free கிடையாது.") - 13.92) < 0.01
    # ...but not after a long pause (or applause) before the next sentence.
    assert abs(t.cut_end(14.0, "வேலை செய்யறேன்.") - 18.1) < 0.01


def test_cut_points_tolerate_punctuation_and_fall_back_to_the_time(tmp_path):
    t = make_transcript(tmp_path, WORDS)
    assert abs(t.cut_start(13.5, "நான் ஒரு ஊழிய") - 13.88) < 0.01
    assert abs(t.cut_start(13.9, "words that are not there") - 13.88) < 0.01


def test_cards_are_mapped_onto_a_clip_made_of_two_parts():
    parts = [[100.0, 110.0], [200.0, 205.0]]
    raw = [{"start": 99.0, "ta": "a", "en": "A"}, {"start": 105.0, "ta": "b", "en": "B"},
           {"start": 200.0, "ta": "c", "en": "C"}]
    assert picker._cards(raw, parts) == [[0.0, 5.0, "a", "A"], [5.0, 10.0, "b", "B"], [10.0, 15.0, "c", "C"]]


def test_a_card_too_short_to_read_is_merged_into_the_previous_one():
    raw = [{"start": 0.0, "ta": "a", "en": ""}, {"start": 4.0, "ta": "b", "en": ""}, {"start": 4.1, "ta": "c", "en": ""}]
    cards = picker._cards(raw, [[0.0, 8.0]])
    assert [c[2] for c in cards] == ["a", "c"]
    assert cards[0][1] == 4.1


def test_plan_drops_clips_of_the_wrong_length(tmp_path):
    t = make_transcript(tmp_path, WORDS)
    raw = {"title_ta": "x", "title_en": "x", "youtube_title": "x", "caption_ta": "", "caption_en": "", "why": "",
           "parts": [{"start": 10, "first_words": "ஆனா உண்மை", "end": 14, "last_words": "வேலை செய்யறேன்."}],
           "cards": [{"start": 10, "ta": "x", "en": "x"}]}
    assert picker.plan_clips([raw], t, 5, 20)[0]["parts"] == [[9.88, 18.1]]
    assert picker.plan_clips([raw], t, 30, 90) == []


def test_schedule_skips_sundays():
    times = posts.schedule(date(2026, 10, 17), 3, time(18, 0))  # a Saturday
    assert [d.strftime("%a %d") for d in times] == ["Sat 17", "Mon 19", "Tue 20"]
    assert times[0].utcoffset().total_seconds() == 7200  # Munich summer time


def test_sermon_date_comes_from_the_title():
    s = Sermon("abc", "யாரை நீ நம்புகிறாய்? - Sept 27 2026 | Tamil Church Munich", datetime(2026, 9, 29))
    assert s.date == "2026-09-27"
    assert s.short_title == "யாரை நீ நம்புகிறாய்?"
    assert Sermon("abc", "Special service", datetime(2026, 9, 29)).date == "2026-09-29"


def test_post_text_and_review_page():
    with open("church.json", encoding="utf-8") as f:
        church = json.load(f)
    clip = {"id": "c1", "title_ta": "தலைப்பு", "title_en": "Title", "youtube_title": "தலைப்பு | Tamil Sermon",
            "caption_ta": "தமிழ்", "caption_en": "English", "why": "Because", "cards": [[0, 2, "அ", "a"]],
            "file": "c1.mp4", "poster": "c1.jpg", "seconds": 42}
    sermon = {"url": "https://youtu.be/x", "folder": "sermons/2026-10-11-x", "short_title": "S", "date": "2026-10-11",
              "clips": [clip]}
    text = posts.post_text(clip, sermon, church, "instagram")
    assert "https://youtu.be/x" in text and "#TamilSermon" in text
    page = review.page([sermon])
    assert 'src="sermons/2026-10-11-x/c1.mp4"' in page and "noindex" in page
