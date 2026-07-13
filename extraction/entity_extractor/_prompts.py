"""EntityExtractor: Prompt- und Muster-Konstanten (Mixin).

Split out of extraction/entity_extractor.py. Behaviour unchanged.
Tests referenzieren z.B. EntityExtractor.METHODS_ONLY_PROMPT ueber die MRO.
"""
from __future__ import annotations



class PromptsMixin:
    """Prompt-Texte, Muster-Kataloge und Regex-Konstanten."""

    STRUCTURAL_PROMPT = """You are the STRUCTURAL extractor for a scientific knowledge graph.

Your task is precise extraction for automatic KG insertion plus separate review candidates.
Do not reason aloud. Do not include markdown.
Return only one complete valid JSON object with exactly these top-level keys:
{
  "concepts": [
    {"label": "canonical named concept", "entity_type": "Algorithm|Theory|MethodFamily|Metric|Dataset|Benchmark|DomainConcept|ApplicationSetting|ModelArchitecture|System|Phenomenon|Task", "context": "where it is materially discussed", "evidence_span": "short source phrase", "section": "paper section if known", "confidence": 0.90, "salience": "central|supporting", "evidence_role": "theory|method_family|metric|dataset|domain_concept"}
  ],
  "methods": [
    {"label": "specific method name", "entity_type": "Algorithm|MethodFamily|ModelArchitecture|System|Task", "domain": "scientific field", "description": "what it does", "evidence_span": "short source phrase", "section": "paper section if known", "source_type": "paper_contribution|reviewed_method|baseline", "salience": "central|supporting"}
  ],
  "concept_candidates": [
    {"label": "candidate concept", "entity_type": "Algorithm|Theory|MethodFamily|Metric|Dataset|Benchmark|DomainConcept|ApplicationSetting|ModelArchitecture|System|Phenomenon|Task", "context": "why it may matter", "evidence_span": "short source phrase", "section": "paper section if known", "confidence": 0.60, "salience": "background|passing", "evidence_role": "background|generic_field|environment|possible_concept"}
  ],
  "method_candidates": [
    {"label": "candidate method", "entity_type": "Algorithm|MethodFamily|ModelArchitecture|System|Task", "domain": "scientific field", "description": "why it may matter", "evidence_span": "short source phrase", "section": "paper section if known", "source_type": "reviewed_method|baseline|background", "confidence": 0.60, "salience": "background|passing"}
  ]
}

Rules:
- Use only the listed entity_type values; do not invent new entity types.
- evidence_span must be a concise phrase or sentence copied/closely paraphrased from this chunk.
- Accepted concepts/methods must be precise enough for automatic KG insertion; uncertain items belong in candidates.
- Return up to 10 accepted concepts and up to 8 accepted methods for this chunk.
- Return up to 8 concept candidates and up to 6 method candidates. If output may become long, omit candidates first.
- Never truncate JSON. A short complete object is better than a long malformed one.
- Put lower-confidence, generic, background, or passing mentions into candidate arrays instead of accepted arrays.
- Prefer named algorithms, model architectures, mathematical frameworks, scientific theories, metrics, datasets, benchmarks, and domain-specific concepts.
- Imaging modalities or acquisition techniques are MethodFamily, not Dataset; reserve Dataset for named corpora, cohorts, collections, or benchmarks.
- Include only items materially discussed in this chunk. Ignore conference names, journal names, section-heading fragments, author lists, and bibliography-only mentions.
- Never group named items under umbrella labels.
- For survey papers, extract the reviewed methods as methods with source_type "reviewed_method"; extract the survey taxonomy/framework as source_type "paper_contribution".
- In survey papers, background algorithms like Q-learning, SARSA, TD(lambda), Dynamic Programming, or generic RL basics are reviewed/background items, not this paper's contribution.
- Method labels must be specific and linkable, not generic labels like "Comparative Analysis" or "Survey Taxonomy".
- Scores must vary by textual certainty: central named items 0.90-0.98, discussed items 0.75-0.89, contextual mentions 0.60-0.74, passing mentions 0.45-0.59.
- Accepted arrays should be compact and high precision. Do not emit duplicate aliases as concepts.
- Generic fields or environments such as Machine Learning, Gridworld, State, or Action Selection should be candidates unless the paper specifically contributes to them.
- Deterministic candidate hints are only hints. Promote a hint only when this chunk materially discusses it.

Deterministic candidate hints:
{candidate_json}

Paper text:
{paper_text}
"""

    SEMANTIC_PROMPT = """You are the SEMANTIC extractor for a scientific knowledge graph.

Use the paper text and the structural extraction context. Do not reason aloud. Do not include markdown.
Your task is meta-level analysis, not additional broad enumeration.
Return only one complete valid JSON object with exactly these top-level keys:
{
  "paper_type": "research|survey|theoretical|benchmark",
  "paper_node": {"title": "paper title if clear", "paper_year": null, "reviewed_period": null},
  "claims": [
    {"statement": "claim made by this paper", "claim_type": "contribution|finding|limitation|negative_result|comparison|recommendation", "evidence_type": "experimental|theoretical|review", "negated": false, "attributed_to": "this_paper"}
  ],
  "cross_domain_hints": [
    {"field": "specific target field", "why_applicable": "method-level transfer reason"}
  ],
  "terminology_conflicts": [
    {"term": "shared term", "this_field": "meaning in this paper", "other_field": "different meaning elsewhere"}
  ],
  "temporal_coverage": {"paper_year": null, "reviewed_period": null},
  "mathematical_content": {"has_formulas": false, "formula_types": []},
  "language_detected": "en"
}

Rules:
- Classify paper_type first: research, survey, theoretical, or benchmark.
- Fill paper_node with title/year/reviewed_period when explicit. The pipeline will add the stable paper_id.
- For research papers, extract claims about this paper's own results only.
- For survey papers, extract field-level meta-claims made by this survey. Do not attribute cited paper results to this paper.
- Use claim_type="limitation" or "negative_result" for weak/null/insufficient results. Set "negated": true only for explicit logical negation such as "does not", "no evidence", or "fails to".
- Cross-domain hints must transfer methods, not just topics.
- Return 3-8 cross-domain hints for survey or theoretical papers when methods could plausibly transfer.
- Terminology conflicts prevent false graph links; include them when a term has materially different meanings across fields.
- Return terminology conflicts for overloaded terms such as reward, value, drive, valence, policy, model, bias, or control when they appear in this paper.
- Detect paper_year and reviewed_period when possible.
- Mark mathematical_content.has_formulas true if the paper contains equations, formal objectives, value functions, reward functions, theorems, proofs, or substantial tables.

Structural extraction context:
{structural_json}

Paper text:
{paper_text}
"""

    METHODS_ONLY_PROMPT = """Extract all named scientific methods from this paper as JSON array.
Each entry: {label, domain, description, source_type}
Paper text: {paper_text}
Respond with only the JSON array, no other text."""

    CONCEPTS_ONLY_PROMPT = """Extract named scientific concepts from this paper as JSON array.
Each entry: {label, entity_type, context, evidence_span, confidence, salience, evidence_role}
Use only these entity_type values: Algorithm, Theory, MethodFamily, Metric, Dataset, Benchmark, DomainConcept, ApplicationSetting, ModelArchitecture, System, Phenomenon, Task.
Prefer paper-specific systems, model architectures, algorithms, datasets, benchmarks, metrics, and scientific concepts.
Treat imaging modalities or acquisition techniques as MethodFamily, not Dataset.
Return complete valid JSON only. A short complete array is better than a malformed long array.
Deterministic candidate hints: {candidate_json}
Paper text: {paper_text}"""

    SEMANTIC_LISTS_RETRY_PROMPT = """Extract scientific claims, cross-domain hints, and terminology conflicts from this paper.
Return only one complete valid JSON object with exactly these keys:
{
  "claims": [
    {"statement": "claim made by this paper", "claim_type": "contribution|finding|limitation|negative_result|comparison|recommendation", "evidence_type": "experimental|theoretical|review", "negated": false, "attributed_to": "this_paper"}
  ],
  "cross_domain_hints": [
    {"field": "specific target field", "why_applicable": "method-level transfer reason"}
  ],
  "terminology_conflicts": [
    {"term": "shared term", "this_field": "meaning in this paper", "other_field": "different meaning elsewhere"}
  ]
}
For survey papers, extract 4-8 field-level meta-claims made by the survey, not individual cited-paper results.
Use claim_type="limitation" or "negative_result" for weak/null/insufficient results. Set "negated": true only for explicit logical negation such as "does not", "no evidence", or "fails to".
Return 3-8 cross-domain hints when methods could plausibly transfer.
Return terminology conflicts for overloaded terms such as reward, value, drive, valence, policy, model, bias, or control when they appear.
Paper text: {paper_text}"""

    KNOWN_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
        ("Q-learning", r"\bQ[\s-]?learning\b"),
        ("SARSA", r"\bSARSA\b"),
        ("TD(lambda)", r"\bTD\s*\(?\s*(?:lambda|\\lambda|λ)\s*\)?|\bTD\s*\(\s*λ\s*\)"),
        ("Reinforcement Learning", r"\breinforcement learning\b"),
        ("TD learning", r"\btemporal difference\s*\(?\s*TD\s*\)?\s+learning\b|\bTD learning\b"),
        ("Temporal difference error", r"\btemporal difference error\b|\bTD error\b"),
        ("Markov Decision Process", r"\bMarkov Decision Process\b"),
        ("MDP", r"\bMDP\b"),
        ("Value function", r"\bvalue function(?:s)?\b"),
        ("Reward function", r"\breward function(?:s)?\b"),
        ("State-action value", r"\bstate-action value\b|\bQ\s*\(\s*s\s*,\s*a\s*\)"),
        ("PPO", r"\bPPO\b|\bProximal Policy Optimization\b"),
        ("A3C", r"\bA3C\b|\bAsynchronous Advantage Actor[-\s]?Critic\b"),
        ("DQN", r"\bDQN\b|\bDeep Q[-\s]?Network(?:s)?\b"),
        ("REINFORCE", r"\bREINFORCE\b"),
        ("Actor-Critic architecture", r"\bActor[-\s]?Critic(?: architecture)?\b"),
        ("Dynamic Programming", r"\bDynamic Programming\b"),
        ("Homeostasis", r"\bhomeostasis\b|\bhomeostatic\b"),
        ("Extrinsic motivation", r"\bextrinsic motivation\b|\bextrinsic/homeostatic\b"),
        ("Intrinsic motivation", r"\bintrinsic motivation\b|\bintrinsic/appraisal\b"),
        ("Motivated reinforcement learning", r"\bmotivated reinforcement learning\b"),
        ("Appraisal theory", r"\bappraisal theor(?:y|ies)\b"),
        ("Prospect Theory", r"\bProspect Theory\b"),
        ("Average reward", r"\baverage reward\b"),
        ("Categorical emotion", r"\bcategorical emotions?\b"),
        ("Dimensional emotion", r"\bdimensional emotions?\b"),
        ("Model-based RL", r"\bmodel[- ]based\s+RL\b|\bmodel[- ]based reinforcement learning\b"),
        ("POMDP", r"\bPOMDP\b|\bPartially Observable Markov Decision Process\b"),
        ("Well-being", r"\bwell[- ]being\b"),
        ("Model uncertainty", r"\bmodel uncertainty\b"),
        ("Novelty", r"\bnovelty\b"),
        ("Recency", r"\brecency\b"),
        ("Control/Power", r"\bcontrol and power\b|\bcontrol/power\b"),
        ("Motivational relevance", r"\bmotivational relevance\b"),
        ("Intrinsic pleasantness", r"\bintrinsic pleasantness\b"),
        ("Social fairness", r"\bsocial fairness\b"),
        ("Social accountability", r"\bsocial accountability\b"),
        ("Valence", r"\bvalence\b|\bvalency\b"),
        ("Arousal", r"\barousal\b"),
        ("Dopamine", r"\bdopamine\b"),
        ("Serotonin", r"\bserotonin\b"),
        ("Noradrenaline", r"\bnoradrenaline\b|\bnorepinephrine\b"),
        ("Acetylcholine", r"\bacetylcholine\b"),
        ("Learning rate", r"\blearning rate\b"),
        ("Discount factor", r"\bdiscount factor\b"),
        ("Boltzmann action selection temperature", r"\bBoltzmann action selection temperature\b"),
        ("Fuzzy logic", r"\bfuzzy logic\b"),
        ("Transition model", r"\btransition models?\b"),
        ("Forward simulation", r"\bforward simulation\b"),
        ("Goal-oriented action planning", r"\bgoal[- ]oriented action planning\b"),
        ("Bio-inspiration", r"\bbio[- ]inspiration\b|\bbio[- ]inspired\b"),
        ("Developmental robotics", r"\bdevelopmental robotics\b"),
        ("Planning community heuristics", r"\bplanning community\b"),
        ("Emotional feedback", r"\bemotional feedback\b"),
        ("Human-robot interaction", r"\bhuman[- ]robot interaction\b"),
        ("Affective modelling", r"\baffective modelling\b|\baffective modeling\b"),
        ("Affective Computing", r"\baffective computing\b"),
        ("Emotion modelling", r"\bemotion(?:al)? model(?:l)?ing\b|\bcomputational emotion models?\b"),
        ("Emotional agents", r"\bemotional agents?\b|\bagents? with emotions?\b"),
        ("Reward shaping", r"\breward shaping\b|\bshap(?:e|ed|ing)\s+rewards?\b"),
        ("Policy gradient", r"\bpolicy gradient(?:s)?\b"),
        ("Value iteration", r"\bvalue iteration\b"),
        ("Multi-agent reinforcement learning", r"\bmulti[- ]agent reinforcement learning\b|\bMARL\b"),
        ("Intrinsic reward", r"\bintrinsic rewards?\b"),
        ("Extrinsic reward", r"\bextrinsic rewards?\b"),
        ("Cognitive appraisal", r"\bcognitive appraisal\b"),
        ("Appraisal dimensions", r"\bappraisal dimensions?\b|\bappraisal variables?\b"),
        ("Human feedback", r"\bhuman feedback\b|\bsocial feedback\b"),
        ("Homeostatic reinforcement learning", r"\bhomeostatic reinforcement learning\b"),
        ("KL-divergence", r"\bKL[- ]divergence\b"),
        ("L1 norm", r"\bL1 norm\b"),
        ("Euclidean distance", r"\bEuclidean distance\b"),
        ("Set point", r"\bset point\b"),
        ("Drive", r"\bdrives?\b"),
        ("Primary reinforcers", r"\bprimary reinforcers\b"),
        ("Emotion elicitation categories", r"\bemotion elicitation categor(?:y|ies)\b"),
        ("Emotion type classification", r"\bemotion type classification\b"),
        ("BERT", r"\bBERT\b"),
        ("RoBERTa", r"\bRoBERTa\b"),
        ("DistilBERT", r"\bDistilBERT\b"),
        ("ELECTRA", r"\bELECTRA\b"),
        ("ELMo", r"\bELMo\b"),
        ("Bi-LSTM", r"\bBi[-\s]?LSTM\b|\bBidirectional LSTM\b|\bBidirectional Long Short[-\s]?Term Memory\b"),
        ("C-LSTM", r"\bC[-\s]?LSTM\b|\bConvolutional LSTM\b"),
        ("Conv-HAN", r"\bConv[-\s]?HAN\b|\bConvolutional Hierarchical Attention Network\b"),
        ("HAN", r"\bHAN\b|\bHierarchical Attention Network\b"),
        ("LSTM", r"\bLSTM\b"),
        ("CNN", r"\bCNN\b|\bConvolutional Neural Network(?:s)?\b"),
        ("Transformer", r"\bTransformer(?:s)?\b"),
        ("GPT", r"\bGPT(?:-\d+(?:\.\d+)?)?\b"),
        ("OCC Model", r"\bOCC\s+model\b|\bOrtony,\s*Clore,\s*(?:and|&)\s*Collins\b"),
        ("PAD Model", r"\bPAD\s+model\b|\bPleasure[-\s]Arousal[-\s]Dominance\b"),
        ("Somatic Marker Hypothesis", r"\bsomatic marker(?: hypothesis)?\b"),
        ("Drive Reduction Theory", r"\bdrive reduction(?: theory)?\b"),
        ("Official Statistics", r"\bofficial statistics\b"),
        ("Data Science", r"\bdata science\b"),
        ("Machine Learning", r"\bmachine learning\b"),
        ("Data Source Changes", r"\bchang(?:e|es|ing)\s+(?:in\s+)?data sources?\b|\bdata sources?\s+chang(?:e|es|ing)\b"),
        ("External Data Sources", r"\bexternal data sources?\b|\balternative data sources?\b"),
        ("Concept Drift", r"\bconcept drift\b"),
        ("Bias", r"\bbias(?:es|ed)?\b"),
        ("Data Availability", r"\bdata availability\b|\bavailability\b"),
        ("Data Validity", r"\bdata validity\b|\bvalidity\b"),
        ("Data Accuracy", r"\bdata accuracy\b|\baccuracy\b"),
        ("Data Completeness", r"\bdata completeness\b|\bcompleteness\b"),
        ("Statistical Neutrality", r"\bstatistical neutrality\b|\bneutrality\b"),
        ("Statistical Reporting", r"\bstatistical reporting\b|\breporting\b"),
        ("Data Source Ownership", r"\bownership\b|\bdata source ownership\b"),
        ("Ethics", r"\bethics?\b|ethical"),
        ("Regulation", r"\bregulation\b|\bregulatory\b"),
        ("Public Perception", r"\bpublic perception\b"),
        ("Privacy", r"\bprivacy\b"),
        ("Robustness", r"\brobustness\b|\brobust\b"),
        ("Monitoring", r"\bmonitoring\b|\bmonitor\b"),
        ("Model Retraining", r"\bretrain(?:ing)?\b|\bmodel retraining\b"),
        ("Data Pipeline", r"\bdata pipelines?\b"),
        ("Data Distribution", r"\bdata distribution\b"),
        ("Derived Data Fields", r"\bderived data fields?\b"),
        ("Data Frequency", r"\bdata frequency\b"),
        ("Data Source Discontinuation", r"\bdiscontinuation\b|\bdiscontinued\b"),
        ("Quantum Machine Learning", r"\bquantum machine learning\b|\bQML\b"),
        ("Photonic Quantum Machine Learning", r"\bphotonic (?:and hybrid )?quantum machine learning\b|\bphotonic QML\b"),
        ("MerLin", r"\bMerLin\b"),
        ("Fock space", r"\bFock[-\s]?space\b|\bFock space\b"),
        ("Linear-optical circuits", r"\blinear[-\s]?optical circuits?\b"),
        ("QuantumLayer", r"\bQuantumLayer\b|\bQuantum Layer\b"),
        ("Angle encoding", r"\bangle encoding\b|\bphase encoding\b"),
        ("Amplitude encoding", r"\bamplitude encoding\b|\bamplitude embedding\b"),
        ("Quantum memristor", r"\bquantum memristors?\b|\bphotonic quantum memristors?\b"),
        ("Fidelity Kernel", r"\bfidelity[-\s]?based kernel\b|\bfidelity kernel\b|\bquantum fidelity kernel\b"),
        ("Adaptive state injection", r"\badaptive state injection\b"),
        ("Quantum Convolutional Neural Network", r"\bQCNNs?\b|\bquantum convolutional neural networks?\b"),
        ("Quantum Generative Adversarial Network", r"\bQGANs?\b|\bquantum generative adversarial networks?\b"),
        ("Quantum Long Short-Term Memory", r"\bQLSTM\b|\bQuantum LSTM\b|\bQuantum Long Short[-\s]?Term Memory\b"),
        ("Quantum Relational Knowledge Distillation", r"\bQRKD\b|\bQuantum Relational Knowledge Distillation\b"),
        ("Strong Linear Optical Simulation", r"\bSLOS\b|\bStrong Linear Optical Simulation\b"),
        ("Quantum Reservoir Computing", r"\bquantum reservoir computing\b|\bquantum optical reservoir computing\b"),
        ("QLOQ", r"\bQLOQ\b"),
        ("MNIST", r"\bMNIST\b"),
        ("CIFAR-10", r"\bCIFAR[-\s]?10\b|\bCIFAR10\b"),
        ("SST2", r"\bSST[-\s]?2\b|\bSST2\b|Stanford Sentiment Treebank 2"),
        ("Temporal entanglement", r"\btemporal entanglement\b"),
        ("Pointer states", r"\bpointer states?\b"),
        ("Synesthesia", r"\bsyn(?:a)?esthesia\b"),
        ("Cross-domain mapping", r"\bcross[-\s]?domain mappings?\b|\bcross[-\s]?modal mappings?\b"),
        ("Unruptured Intracranial Aneurysm", r"\bUIAs?\b|\bunruptured intracranial aneurysms?\b"),
        ("TOF-MRA", r"\bTOF[-\s]?MRA\b|\btime[-\s]?of[-\s]?flight magnetic resonance angiography\b"),
        ("ADAM dataset", r"\bAneurysm Detection And segMentation\b|\bADAM dataset\b"),
        ("ADAM challenge", r"\bADAM challenge\b"),
        ("PHASES score", r"\bPHASES score\b"),
        ("Computer-aided detection", r"\bcomputer[-\s]?aided detection\b|\bCAD system\b|\bCAD tool\b"),
        ("3D U-Net", r"\b3D[-\s]?U[-\s]?Net\b|\b3D UNET\b"),
        ("Satisfaction of Search", r"\bsatisfaction[-\s]?of[-\s]?search(?: effect)?\b"),
        ("McNemar's test", r"\bMcNemar[’']?s test\b"),
        ("Wilcoxon signed-rank test", r"\bWilcoxon signed[-\s]?rank tests?\b"),
    )

    RL_EMOTION_LABELS = {
        "Homeostasis",
        "Extrinsic motivation",
        "Intrinsic motivation",
        "Motivated reinforcement learning",
        "Appraisal theory",
        "Prospect Theory",
        "Average reward",
        "Categorical emotion",
        "Dimensional emotion",
        "Model-based RL",
        "POMDP",
        "Well-being",
        "Model uncertainty",
        "Novelty",
        "Recency",
        "Control/Power",
        "Motivational relevance",
        "Intrinsic pleasantness",
        "Social fairness",
        "Social accountability",
        "Valence",
        "Arousal",
        "Dopamine",
        "Serotonin",
        "Noradrenaline",
        "Acetylcholine",
        "Learning rate",
        "Discount factor",
        "Boltzmann action selection temperature",
        "Fuzzy logic",
        "Transition model",
        "Forward simulation",
        "Goal-oriented action planning",
        "Bio-inspiration",
        "Developmental robotics",
        "Planning community heuristics",
        "Emotional feedback",
        "Human-robot interaction",
        "Affective modelling",
        "Affective Computing",
        "Emotion modelling",
        "Emotional agents",
        "Reward shaping",
        "Policy gradient",
        "Value iteration",
        "Multi-agent reinforcement learning",
        "Intrinsic reward",
        "Extrinsic reward",
        "Cognitive appraisal",
        "Appraisal dimensions",
        "Human feedback",
        "Homeostatic reinforcement learning",
        "KL-divergence",
        "L1 norm",
        "Euclidean distance",
        "Set point",
        "Drive",
        "Primary reinforcers",
        "Emotion elicitation categories",
        "Emotion type classification",
    }

    OFFICIAL_STATISTICS_LABELS = {
        "Official Statistics",
        "Data Source Changes",
        "External Data Sources",
        "Concept Drift",
        "Bias",
        "Data Availability",
        "Data Validity",
        "Data Accuracy",
        "Data Completeness",
        "Statistical Neutrality",
        "Statistical Reporting",
        "Data Source Ownership",
        "Ethics",
        "Regulation",
        "Public Perception",
        "Privacy",
        "Robustness",
        "Monitoring",
        "Model Retraining",
        "Data Pipeline",
        "Data Distribution",
        "Derived Data Fields",
        "Data Frequency",
        "Data Source Discontinuation",
    }

    QML_LABELS = {
        "Quantum Machine Learning",
        "Photonic Quantum Machine Learning",
        "MerLin",
        "Fock space",
        "Linear-optical circuits",
        "QuantumLayer",
        "Angle encoding",
        "Amplitude encoding",
        "Quantum memristor",
        "Fidelity Kernel",
        "Adaptive state injection",
        "Quantum Convolutional Neural Network",
        "Quantum Generative Adversarial Network",
        "Quantum Long Short-Term Memory",
        "Quantum Relational Knowledge Distillation",
        "Strong Linear Optical Simulation",
        "Quantum Reservoir Computing",
        "QLOQ",
        "MNIST",
        "CIFAR-10",
        "SST2",
    }

    GENERIC_ACCEPTED_CONCEPT_BLOCKLIST = {
        "Machine Learning",
        "Gridworld",
        "Prey and predators",
        "Mazes",
        "State",
        "Action selection",
        "Exploration",
        "Transparency",
    }

    SURVEY_BACKGROUND_METHODS = {
        "Q-learning",
        "SARSA",
        "TD(lambda)",
        "Dynamic Programming",
        "TD learning",
        "Value iteration",
        "Policy gradient",
        "Reward shaping",
        "Actor-Critic",
    }

    TITLE_STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "based",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "its",
        "of",
        "on",
        "or",
        "our",
        "the",
        "to",
        "using",
        "via",
        "with",
    }

    MATH_PATTERNS: tuple[tuple[str, str], ...] = (
        ("formula", r"\$[^$]{1,300}\$|\\\[[\s\S]{1,600}?\\\]|\\begin\{equation\}"),
        ("formula", r"\bequation\b|\bformula\b"),
        ("theorem", r"\btheorem\b|\bproof\b"),
        ("table", r"\bTable\s+\d+\b"),
        ("reward_function", r"\breward function\b|\bR\s*\(\s*s\s*,\s*a"),
        ("value_function", r"\bvalue function\b|\bV\s*\(\s*s\s*\)|\bQ\s*\(\s*s\s*,\s*a\s*\)"),
        ("optimization_objective", r"\bloss function\b|\bobjective function\b|\barg\s*max\b|\barg\s*min\b"),
        ("probabilistic_model", r"\bBayesian\b|\bprobabilistic\b|\bp\s*\(\s*[^)]+\s*\)"),
    )

    LEGACY_ARXIV_CATEGORY_RE = (
        r"(?:astro-ph|cond-mat|cs|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|"
        r"math-ph|math|nlin|nucl-ex|nucl-th|physics|q-bio|q-fin|quant-ph|stat)"
    )
