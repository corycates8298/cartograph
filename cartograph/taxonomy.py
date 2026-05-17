"""
Fragrance Taxonomy — Maps BCS fragrance names to scent note profiles.
This is the foundation for vector similarity matching.

Each fragrance has:
- name: The marketing name (as it appears in POS data)
- family: High-level scent family (Floral, Citrus, Fresh, Woody, Sweet, Gourmand, Herbal, Fruity)
- notes: Descriptive scent notes used for embedding
- tags: Additional searchable attributes
"""

# Scent families for classification
FAMILIES = [
    "Floral",      # Rose, jasmine, lavender, magnolia
    "Citrus",      # Lemon, orange, grapefruit, bergamot
    "Fresh",       # Clean, cotton, aquatic, ozone
    "Woody",       # Sandalwood, cedar, patchouli, oud
    "Sweet",       # Vanilla, caramel, sugar, honey
    "Gourmand",    # Edible scents — bakery, fruit dessert, candy
    "Herbal",      # Eucalyptus, mint, sage, rosemary
    "Fruity",      # Berry, apple, mango, peach (non-citrus fruit)
]

# Full BCS fragrance taxonomy
# Format: name → {family, notes (for embedding), tags}
FRAGRANCE_TAXONOMY = {
    "99 PomLems": {
        "family": "Citrus",
        "notes": "pomegranate, lemon zest, bright citrus, tart fruit, sparkling",
        "tags": ["energizing", "tart", "bright"],
    },
    "All Hail The Queen": {
        "family": "Floral",
        "notes": "regal floral bouquet, rose, peony, elegant perfume, feminine musk",
        "tags": ["luxurious", "feminine", "floral"],
    },
    "Aloe + Clover": {
        "family": "Fresh",
        "notes": "fresh aloe vera, green clover, dewy grass, clean botanical, crisp",
        "tags": ["clean", "green", "light"],
    },
    "Alter Ego": {
        "family": "Woody",
        "notes": "dark mysterious musk, leather, amber, smoky wood, sensual",
        "tags": ["bold", "masculine", "dark"],
    },
    "Apple Mango": {
        "family": "Fruity",
        "notes": "crisp green apple, ripe tropical mango, sweet fruit, juicy",
        "tags": ["tropical", "sweet", "bright"],
    },
    "Aqua Spa": {
        "family": "Fresh",
        "notes": "ocean water, sea salt, clean mineral, aquatic breeze, refreshing spa",
        "tags": ["clean", "aquatic", "relaxing"],
    },
    "Breathe": {
        "family": "Herbal",
        "notes": "eucalyptus, peppermint, menthol, clear airways, cooling medicinal",
        "tags": ["medicinal", "cooling", "invigorating"],
    },
    "Cherry Almond": {
        "family": "Gourmand",
        "notes": "sweet cherry, toasted almond, marzipan, bakery warmth, nutty fruit",
        "tags": ["sweet", "nutty", "bakery"],
    },
    "Cobalt Blue": {
        "family": "Fresh",
        "notes": "crisp masculine cologne, blue aquatic, ozonic, cool water, clean musk",
        "tags": ["masculine", "cologne", "clean"],
    },
    "Coconut": {
        "family": "Gourmand",
        "notes": "creamy coconut milk, tropical coconut flesh, sweet island, warm",
        "tags": ["tropical", "creamy", "warm"],
    },
    "Commando": {
        "family": "Woody",
        "notes": "rugged masculine cologne, cedar, leather, fresh bergamot, bold musk",
        "tags": ["masculine", "bold", "fresh"],
    },
    "Eucalyptus": {
        "family": "Herbal",
        "notes": "pure eucalyptus leaf, camphor, green medicinal, spa steam, minty",
        "tags": ["medicinal", "spa", "green"],
    },
    "Ferocious Beast": {
        "family": "Woody",
        "notes": "fierce masculine musk, dark amber, smoky wood, savage intensity",
        "tags": ["intense", "masculine", "smoky"],
    },
    "Fresh Cotton": {
        "family": "Fresh",
        "notes": "clean laundry, fresh linen, soft cotton, dryer sheets, airy white musk",
        "tags": ["clean", "laundry", "soft"],
    },
    "Fruits with Benefits": {
        "family": "Fruity",
        "notes": "mixed berry medley, strawberry, raspberry, juicy fruit cocktail, sweet",
        "tags": ["berry", "sweet", "playful"],
    },
    "Fruity Loopy": {
        "family": "Gourmand",
        "notes": "fruit cereal, sweet milk, sugary loops, nostalgic breakfast, candy fruit",
        "tags": ["nostalgic", "sweet", "playful"],
    },
    "Good Morning Sunshine": {
        "family": "Citrus",
        "notes": "bright morning citrus, orange juice, sunny grapefruit, energizing zest",
        "tags": ["energizing", "bright", "morning"],
    },
    "Happy": {
        "family": "Floral",
        "notes": "light joyful floral, lily of valley, spring garden, airy happiness, fresh green",
        "tags": ["light", "uplifting", "spring"],
    },
    "Hey Headache": {
        "family": "Herbal",
        "notes": "peppermint, lavender, tension relief, cooling menthol, calming herbal",
        "tags": ["medicinal", "calming", "cooling"],
    },
    "Island Nectar": {
        "family": "Fruity",
        "notes": "tropical island fruits, passionfruit, guava, sweet nectar, paradise",
        "tags": ["tropical", "exotic", "sweet"],
    },
    "Kraken": {
        "family": "Fresh",
        "notes": "deep ocean, sea spray, driftwood, aquatic masculine, salt air, cool depths",
        "tags": ["aquatic", "masculine", "bold"],
    },
    "Lavender": {
        "family": "Floral",
        "notes": "french lavender fields, calming purple floral, herbal relaxation, soothing",
        "tags": ["calming", "classic", "herbal"],
    },
    "Lemongrass + Eucalyptus": {
        "family": "Herbal",
        "notes": "bright lemongrass, eucalyptus leaf, citrusy herbal, spa fresh, invigorating",
        "tags": ["spa", "energizing", "citrus-herbal"],
    },
    "Life of the Party": {
        "family": "Sweet",
        "notes": "sweet celebration, champagne fizz, sugary fun, sparkling vanilla, confetti",
        "tags": ["festive", "sweet", "sparkling"],
    },
    "Love Potion": {
        "family": "Floral",
        "notes": "romantic rose, seductive jasmine, sweet musk, passionate floral, velvety",
        "tags": ["romantic", "sensual", "feminine"],
    },
    "Magnolia": {
        "family": "Floral",
        "notes": "southern magnolia blossom, creamy white floral, lush garden, elegant",
        "tags": ["elegant", "southern", "creamy"],
    },
    "Muse": {
        "family": "Floral",
        "notes": "artistic floral blend, ethereal white flowers, creative inspiration, dreamy",
        "tags": ["artistic", "dreamy", "unique"],
    },
    "Narcissist": {
        "family": "Sweet",
        "notes": "indulgent luxury, sweet amber, golden vanilla, self-love musk, opulent",
        "tags": ["luxurious", "indulgent", "warm"],
    },
    "Oatmeal + Honey": {
        "family": "Gourmand",
        "notes": "warm oatmeal, golden honey, comforting bakery, gentle sweet, soothing",
        "tags": ["comforting", "gentle", "warm"],
    },
    "Patchouli": {
        "family": "Woody",
        "notes": "earthy patchouli, deep green herbal, hippie musk, grounding wood",
        "tags": ["earthy", "bohemian", "deep"],
    },
    "Patchouli Sandalwood": {
        "family": "Woody",
        "notes": "earthy patchouli, creamy sandalwood, warm exotic wood, grounding meditation",
        "tags": ["earthy", "creamy", "grounding"],
    },
    "Peach Mimosa": {
        "family": "Fruity",
        "notes": "ripe juicy peach, sparkling champagne, brunch cocktail, bubbly fruit",
        "tags": ["bubbly", "brunch", "feminine"],
    },
    "Persnickety": {
        "family": "Fresh",
        "notes": "precise clean blend, crisp perfection, immaculate freshness, particular musk",
        "tags": ["precise", "clean", "particular"],
    },
    "Pink Sugar": {
        "family": "Sweet",
        "notes": "cotton candy, pink spun sugar, sweet carnival, girly vanilla, playful",
        "tags": ["playful", "girly", "candy"],
    },
    "Sandalwood": {
        "family": "Woody",
        "notes": "creamy indian sandalwood, warm milky wood, meditation, smooth exotic",
        "tags": ["warm", "exotic", "smooth"],
    },
    "Sunshine": {
        "family": "Citrus",
        "notes": "warm sunshine citrus, golden rays, bright lemon, cheerful orange peel",
        "tags": ["cheerful", "bright", "warm"],
    },
    "Unscented": {
        "family": "Fresh",
        "notes": "no fragrance, neutral, sensitive skin, fragrance free, plain clean",
        "tags": ["sensitive", "neutral", "plain"],
    },
    "White Jasmine": {
        "family": "Floral",
        "notes": "pure white jasmine, night-blooming floral, intoxicating sweet petals, exotic",
        "tags": ["exotic", "night", "intoxicating"],
    },
    # Additional BCS fragrances (from full product line)
    "Beach": {
        "family": "Fresh",
        "notes": "warm sand, ocean breeze, coconut sunscreen, salt air, summer vacation",
        "tags": ["summer", "vacation", "warm"],
    },
    "Brown Sugar Fig": {
        "family": "Gourmand",
        "notes": "caramelized brown sugar, ripe fig, autumn warmth, bakery spice, rich",
        "tags": ["autumn", "rich", "spiced"],
    },
    "Coconut Cream": {
        "family": "Gourmand",
        "notes": "rich coconut cream, vanilla custard, tropical dessert, silky sweet",
        "tags": ["creamy", "tropical", "dessert"],
    },
    "Cotton Candy": {
        "family": "Sweet",
        "notes": "spun sugar, carnival sweetness, pink fluffy candy, pure sugar cloud",
        "tags": ["playful", "sweet", "nostalgic"],
    },
    "Eucalyptus Mint": {
        "family": "Herbal",
        "notes": "eucalyptus leaf, fresh spearmint, cooling green, spa aromatherapy",
        "tags": ["cooling", "spa", "invigorating"],
    },
    "Grapefruit Mimosa": {
        "family": "Citrus",
        "notes": "tart pink grapefruit, sparkling champagne, brunch citrus, bubbly zest",
        "tags": ["tart", "bubbly", "brunch"],
    },
    "Honeysuckle": {
        "family": "Floral",
        "notes": "sweet honeysuckle vine, nectar, southern garden, warm floral honey",
        "tags": ["southern", "sweet", "garden"],
    },
    "Japanese Cherry Blossom": {
        "family": "Floral",
        "notes": "delicate cherry blossom, spring sakura, light asian floral, soft pink petals",
        "tags": ["delicate", "spring", "asian"],
    },
    "Lemon Drop": {
        "family": "Citrus",
        "notes": "bright lemon candy, sugary citrus, tart sweet drop, zesty sunshine",
        "tags": ["bright", "candy", "zesty"],
    },
    "Mango Tango": {
        "family": "Fruity",
        "notes": "ripe mango, tropical dance, sweet exotic fruit, juicy sunshine",
        "tags": ["tropical", "exotic", "juicy"],
    },
    "Peppermint": {
        "family": "Herbal",
        "notes": "pure peppermint, cooling menthol, candy cane, fresh breath, crisp",
        "tags": ["cooling", "crisp", "holiday"],
    },
    "Rosemary Sage": {
        "family": "Herbal",
        "notes": "garden rosemary, white sage, herbaceous green, aromatic culinary, earthy",
        "tags": ["garden", "earthy", "aromatic"],
    },
    "Warm Vanilla": {
        "family": "Sweet",
        "notes": "warm madagascar vanilla, sweet cream, comforting amber, cozy blanket",
        "tags": ["comforting", "cozy", "classic"],
    },
}


def get_all_fragrances():
    """Return list of all fragrance names."""
    return list(FRAGRANCE_TAXONOMY.keys())


def get_notes(name):
    """Get scent notes for a fragrance name."""
    entry = FRAGRANCE_TAXONOMY.get(name)
    if entry:
        return entry["notes"]
    # Fuzzy match — try case-insensitive, strip "Plus"
    clean = name.strip().replace("  Plus", "").replace(" Plus", "")
    for key, val in FRAGRANCE_TAXONOMY.items():
        if key.lower() == clean.lower():
            return val["notes"]
    return None


def get_family(name):
    """Get scent family for a fragrance name."""
    entry = FRAGRANCE_TAXONOMY.get(name)
    if entry:
        return entry["family"]
    clean = name.strip().replace("  Plus", "").replace(" Plus", "")
    for key, val in FRAGRANCE_TAXONOMY.items():
        if key.lower() == clean.lower():
            return val["family"]
    return "Unknown"


def get_taxonomy_for_embedding():
    """Return list of (name, notes_text) tuples ready for embedding."""
    return [(name, data["notes"]) for name, data in FRAGRANCE_TAXONOMY.items()]
