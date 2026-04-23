from fpt_monitor import scheduled_report
from datetime import datetime

if __name__ == "__main__":
    label = "Kết thúc ca sáng 11:30" if datetime.now().hour < 12 else "Kết thúc ca chiều 14:30"
    scheduled_report(label)
