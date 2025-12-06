import os

class Config:
    """Configuration settings for the scheduling system"""
    
    # Gemini API Configuration
    GEMINI_API_KEY = "[GEMINI_API_KEY]"
    GEMINI_MODEL = "gemini-2.5-flash"
    
    # Gurobi WLS License Configuration
    GUROBI_WLS_LICENSE_ID = "[GUROBI_WLS_LICENSE_ID]"
    
    # Storage settings
    STORAGE_DIR = "scheduling_runs"
    
    # Explanation settings
    MAX_EXPLANATION_TOKENS = 4000
    TEMPERATURE = 0.7
    
    # Solver selection: "julia" (default) or "mock" (for testing without Julia)
    SOLVER_TYPE = os.environ.get("SOLVER_TYPE", "julia")
    
    @classmethod
    def setup_gurobi_license(cls):
        """Configure Gurobi WLS license from config"""
        os.environ['WLSACCESSID'] = cls.GUROBI_WLS_LICENSE_ID
        os.environ['WLSSECRET'] = ''
        os.environ['LICENSEID'] = cls.GUROBI_WLS_LICENSE_ID
    
    @classmethod
    def ensure_storage_dir(cls):
        """Create storage directory if it doesn't exist"""
        os.makedirs(cls.STORAGE_DIR, exist_ok=True)