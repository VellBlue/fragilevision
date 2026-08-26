# Contributing

FragileVision accepts changes that make evaluation claims more transparent, reproducible or falsifiable.

Before submitting a change:

1. explain which measurement failure it prevents;
2. add a deterministic test;
3. keep runtime dependencies at zero unless the capability cannot reasonably be implemented otherwise;
4. never include private datasets or endpoint credentials;
5. distinguish a new metric from a renamed aggregate.

Run:

```bash
python3 -m unittest discover -s tests -v
node --check fragilevision/static/app.js
```

UI changes should remain usable at 320 px width and with `prefers-reduced-motion` enabled.

