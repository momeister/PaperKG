import { domAnimation } from "framer-motion";

// Eigene Datei, damit Vite die Animations-Features als eigenen Chunk abspaltet
// (LazyMotion lädt sie nach; der Entry bleibt schlank).
export default domAnimation;
