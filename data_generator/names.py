"""Name pools (no external dependency such as Faker). All people and clients are fictional."""

INDIAN_FIRST = [
    "Aarav", "Aditi", "Aditya", "Akash", "Akshay", "Amit", "Ananya", "Anjali", "Ankit", "Anushka", "Arjun", "Aryan", "Ayesha",
    "Bhavna", "Chetan", "Deepak", "Deepika", "Devansh", "Divya", "Farhan", "Gaurav", "Gayatri", "Harsh", "Harini", "Ishaan",
    "Ishita", "Jatin", "Jyoti", "Karan", "Kavya", "Keerthi", "Kiran", "Kunal", "Lakshmi", "Manish", "Meera", "Mohit", "Nandini",
    "Naveen", "Neha", "Nikhil", "Nisha", "Pallavi", "Pooja", "Pradeep", "Pranav", "Priya", "Rahul", "Rajesh", "Rakesh", "Ritika",
    "Rohan", "Sagar", "Sakshi", "Sameer", "Sanjana", "Saurabh", "Shreya", "Shruti", "Siddharth", "Simran", "Sneha", "Soumya",
    "Subham", "Sunil", "Swati", "Tanvi", "Tanya", "Tarun", "Uday", "Varun", "Vidya", "Vikas", "Vikram", "Vinay", "Vishal",
    "Yash", "Zoya", "Abhishek", "Aishwarya", "Debashish", "Sourav", "Ipsita", "Subhashree", "Lipsa", "Biswajit", "Smruti",
    "Karthik", "Lavanya", "Srinivas", "Venkatesh", "Madhavi", "Suresh", "Ramya", "Prakash", "Sowmya", "Harpreet", "Gurpreet",
    "Manpreet", "Imran", "Sana", "Faizan", "Mehul", "Hetal", "Dhruv", "Riya", "Om", "Parth", "Kriti", "Ayaan", "Vivaan",
]
INDIAN_LAST = [
    "Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Shah", "Mehta", "Joshi", "Reddy", "Rao", "Naidu", "Iyer", "Iyengar",
    "Nair", "Menon", "Pillai", "Das", "Mohanty", "Patnaik", "Mishra", "Panda", "Sahoo", "Behera", "Nayak", "Banerjee", "Chatterjee",
    "Mukherjee", "Ghosh", "Bose", "Sen", "Kulkarni", "Deshpande", "Patil", "Jadhav", "Pawar", "Chauhan", "Rathore", "Yadav",
    "Tiwari", "Pandey", "Dubey", "Srivastava", "Saxena", "Agarwal", "Jain", "Bansal", "Malhotra", "Kapoor", "Khanna", "Arora",
    "Bhatia", "Sethi", "Gill", "Sandhu", "Khan", "Ansari", "Qureshi", "Siddiqui", "Fernandes", "D'Souza", "Pereira", "Thomas",
    "Varghese", "Krishnan", "Subramanian", "Venkataraman", "Raghavan", "Hegde", "Shetty", "Kamath", "Bhat", "Chaudhary", "Thakur",
]
INTL_FIRST = ["James", "Emily", "Michael", "Sarah", "David", "Laura", "Daniel", "Emma", "Lucas", "Sophie", "Liam", "Olivia",
              "Noah", "Mia", "Ethan", "Hannah", "Mateo", "Chloe", "Jonas", "Lena", "Kenji", "Yuki", "Wei", "Mei"]
INTL_LAST = ["Smith", "Johnson", "Brown", "Miller", "Davis", "Wilson", "Taylor", "Clark", "Walker", "Young", "Müller", "Schmidt",
             "Fischer", "de Vries", "Jansen", "Martin", "Bernard", "Tanaka", "Sato", "Chen", "Wong", "Lim", "Tan", "O'Brien"]

# invented client names: syllable + syllable + industry suffix
CLIENT_SYL_A = ["Vel", "Zan", "Kor", "Mir", "Tal", "Quen", "Bryn", "Ast", "Lum", "Cyr", "Dex", "Ely", "Fen", "Gal", "Hal",
                "Iso", "Jor", "Kel", "Lyr", "Nor", "Orv", "Pyr", "Rav", "Sol", "Tor", "Ulm", "Vor", "Wen", "Xal", "Yor", "Zel", "Bel"]
CLIENT_SYL_B = ["ora", "ix", "ent", "ara", "ion", "ova", "eon", "isa", "une", "eth", "alis", "antis", "ero", "ium", "yra", "ondo"]
CLIENT_SUFFIX = {
    "BFS": ["Bank", "Financial", "Capital", "Bancorp", "Payments", "Credit Union"],
    "INS": ["Insurance", "Mutual", "Assurance", "Re", "Life"],
    "HLS": ["Health", "Healthcare", "Life Sciences", "Medical", "Pharma", "Care Network"],
    "TTA": ["Airways", "Air", "Aviation", "Travel Group", "Logistics", "Rail"],
    "RCG": ["Retail", "Stores", "Brands", "Foods", "Mart", "Consumer"],
    "MFG": ["Motors", "Industries", "Manufacturing", "Automotive", "Engineering"],
    "ENU": ["Energy", "Power", "Utilities", "Grid", "Renewables"],
    "AGR": ["AgriFoods", "Farms", "Agritech", "Seeds", "Crop Sciences", "Agro"],
    "TMT": ["Telecom", "Communications", "Media", "Networks", "Broadcasting"],
}
