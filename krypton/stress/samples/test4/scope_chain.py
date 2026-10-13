def outer(a, b, c):
    if a:
        def inner(x):
            if b:
                f = lambda y: (lambda z: z if c else 0)(y) if y else None
                return f(x)
            return None
        return inner(1)
    return None
