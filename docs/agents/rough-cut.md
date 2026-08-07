# The rough-cut pillar

How a rough cut gets assembled: the two documents you author before cutting, the loop you
cut in, and the self-review that has to pass before the director sees anything.

This is P4 of spec #22 (stories 51–55). It is the other shape from the concert pillar —
no continuous master mix, no beat grid, no angle roles. The substrate is one or more
A-roll cameras talking, and the evidence you cut from is a word-level transcript.

## The two documents you own

Both are yours. The server never reads or writes either one: the only way b-roll reaches a
timeline is as an `overlays[]` entry in the cut file you author, and the only way the brief
reaches anything is by changing what you decide. `tests/test_rough_cut_pillar.py` guards
that — no module under `src/` names a brief or a catalog by path.

They live per project:

```
projects/<project>/brief.md      # what the director wants this to be
projects/<project>/broll.json    # what you have to cover with
```

### The brief

Markdown, short, written with the director before the first assembly. It settles the
questions that change the shape of the whole cut, not the taste ones you can resolve as you
go. The one that always matters:

- **Script order or narrative reorder.** Script order means the delivered piece follows the
  order it was shot in and your job is take selection and tightening. Narrative reorder
  means you may move material, and the outline you write to do that is an artifact of your
  working, not a gate — the director does not sign it off before you cut.

Anything else worth stating goes underneath as prose: target length, who the audience is,
whether filler and breath belong in the register or not, what must survive uncut.

```markdown
# brief — founder-interview

Order: narrative reorder. The origin story leads; the funding answer moves after it.
Length: 6–8 minutes, no hard cap.
Register: conversational. Leave the ums that carry thought; cut the ones that stall.
Must survive: the answer about the first hire, whole.
```

### The b-roll catalog

JSON, one entry per b-roll clip, built by grabbing frames and labelling what you see —
`grab_frames` at a few points per clip, then write down what the clip is *about*. Grab and
label once per project; the catalog is what makes coverage planned rather than sprinkled.

```jsonc
{
  "schema": 1,
  "clips": [
    {
      "alias": "broll_hands",          // the source alias you will use in the cut file
      "clip": "B012.mp4",              // media-pool clip name
      "bin": "B-roll",
      "usable": [{ "in": 0, "out": 240 }],   // ranges worth cutting, source frames
      "topics": ["typing", "desk", "hands"], // what it is about — how you match it
      "note": "slow push in; the last 40 frames wobble"
    }
  ]
}
```

`topics` is the whole point: covering a jump cut means reaching for a clip that is about
what is being said across that jump, not for whatever is next in the bin. Nothing validates
these strings — they are notes to yourself, and they are only useful if they describe the
subject rather than the shot.

## The loop

1. **Transcribe every A-roll source.** `transcribe_audio` per source; keep the paths.
2. **Read the transcripts.** Word-level, with confidence and silence spans. Flubs, retakes
   and filler are yours to spot by reading — no tool labels them, deliberately (see
   `analysis/transcript.py`: "Nothing is called a flub"). Take selection is judgement over
   evidence.
3. **Assemble the A-roll** into a cut file of sequential segments, in brief order.
4. **Park the retakes as alternates.** A line delivered three times is one segment with two
   `alternates[]`, not three segments. Alternates must match the main frame for frame (E8),
   so offer the other take at the chosen take's length. That is what lets the director flip
   a take during review with `swap_take` instead of asking for a rebuild.
5. **Cover the jump cuts.** Two segments from the same source, back to back, are a visible
   jump. Anchor an overlay from the catalog over the earlier segment at an offset that puts
   it across the seam — anchored, so it survives a tightening pass. Cutting from one camera
   to another is not a jump cut and needs no cover.
6. **`validate_cut`, then `build_timeline`.** Every revision is a new `<name> v<N>`.
7. **Self-review with `virtual_transcript`.** Below.
8. **Fix what it found, rebuild, and mark the rest** as the cut report.

## The self-review

`virtual_transcript` reads the cut file back as the words the built timeline will contain.
Pass it the cut file and a mapping of source alias to the transcript document for that
source. Run it before the director sees the cut, every version. It is the P4 counterpart to
the mandatory `correlate_timeline` pass on a concert cut.

Read `text` first — that is the piece, as prose. If it does not read as something a person
said, nothing else matters yet.

Then work the warnings. There are no errors; this refuses nothing:

| Rule | What it found | What it usually means |
| --- | --- | --- |
| W1 | a word cut in half | the boundary is a frame or two off; the fix hint names the frame |
| W2 | the same run of words on both sides of a seam | two takes of one line both survived |
| W3 | a segment's source has no transcript | either transcribe it, or it is silent on purpose |
| W4 | a low-confidence word survives into the cut | listen to it before delivering |
| W5 | an uncovered seam between two shots of one source | a jump cut with no b-roll over it |

W2 is the one to take seriously: a cut file that keeps a false start in front of the good
take is structurally perfect — it validates, it builds, it plays — and only reading the
words back says the piece stammers.

Every warning is a reading, not a verdict. A repeat can be deliberate. A mid-word cut can
be the point. Filler is not flagged at all, because whether an "um" carries thought or
stalls is exactly the judgement the brief settles and no detector should.

## The cut report

What the director gets is the built timeline plus markers on it — uncertainties only, not a
running commentary. The W4 warnings are the natural contents: each one is a word you are
delivering that the transcriber was unsure of, at a frame the director can jump to. Add
anything you decided against the obvious reading, and leave everything you are confident
about unmarked. Reviewing a rough cut should take minutes.

Then the review round is the ordinary one: the director's notes come back as markers,
`list_markers` is your queue, take notes are `swap_take` with the cut file kept in sync, and
everything else is a new version.
