"""
prd_generator.py — Daily PRD (Product Requirements Document) generator.

Picks a random product from a curated list and generates a detailed PRD
using Ollama/Mistral (free, local) or falls back to a structured template.
Stores each PRD as a JSON file in data/prds/.
"""

import os
import json
import random
import logging
import hashlib
from datetime import datetime, date

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRD_DIR  = os.path.join(BASE_DIR, "data", "prds")
os.makedirs(PRD_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Product catalogue — 365 products ordered by complexity (Day 1 = simplest)
# Tier 1  (days   1–30): Single-purpose consumer utilities
# Tier 2  (days  31–60): Consumer apps with payments / basic social
# Tier 3  (days  61–90): Simple two-sided marketplaces
# Tier 4  (days  91–120): SaaS tools for SMBs & professionals
# Tier 5  (days 121–150): Social & community platforms with network effects
# Tier 6  (days 151–180): Intermediate multi-feature B2B SaaS
# Tier 7  (days 181–210): Technical products with basic AI / ML
# Tier 8  (days 211–240): Complex multi-sided platforms with trust/verification
# Tier 9  (days 241–270): Complex SaaS with deep integrations & compliance
# Tier 10 (days 271–300): AI-heavy enterprise products
# Tier 11 (days 301–330): Platform / ecosystem / developer-API products
# Tier 12 (days 331–365): Infrastructure, real-time systems & regulatory tech
# ─────────────────────────────────────────────────────────────────────────────
PRODUCTS = [

    # ════════════════════════════════════════════════════════════════════════
    # TIER 1 — Days 1-30 — Single-purpose consumer utilities (no network effects, no payments)
    # ════════════════════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════════════════════
    # TIER 1 — Days 1–30 — Single-purpose consumer utilities
    # One clear job, zero network effects, no payments, familiar problem
    # Complexity score: 1  |  Good for: User problems, Jobs-to-be-Done, MVP scoping
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Daily Water Intake Tracker", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Reminds you to drink water at intervals and logs daily hydration progress"},
    {"name": "Medicine Reminder App", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Alerts patients to take medicines on time and logs missed doses for family caregivers"},
    {"name": "Expense Notepad", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Tap-to-add daily expense log with category tags and monthly totals — no bank link needed"},
    {"name": "Bill Due Date Tracker", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Reminds you 3 days before electricity, rent, EMI, and subscription payments are due"},
    {"name": "Daily Step Counter & Goal Setter", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Counts steps via phone sensors and nudges you when you fall behind your daily goal"},
    {"name": "Vocabulary Word of the Day", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Delivers one new English or Hindi word daily with meaning, example, and a quiz"},
    {"name": "Book Reading Tracker", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Log books you've read, track pages per day, and set yearly reading targets"},
    {"name": "Personal Diary & Mood Journal", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Private daily journal with mood tagging to help you notice emotional patterns over time"},
    {"name": "Prayer / Namaz Time App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Accurate location-based prayer times with Azan alerts and Qibla compass"},
    {"name": "Birthday & Anniversary Reminder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Never forget a birthday — syncs with contacts and nudges you a week before"},
    {"name": "Public Holiday Calendar India", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "State-wise public holiday list with long-weekend planner and iCal export"},
    {"name": "Recipe Book App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Save family recipes with photos, ingredients, and steps — fully offline"},
    {"name": "Food Calorie Lookup", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Search 5,000+ Indian dishes and packaged foods for calorie and macro info"},
    {"name": "Plant Care Reminder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Tells you when to water, fertilise, and repot each plant based on species and season"},
    {"name": "Morning Routine Builder", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Build a timed morning routine checklist and track your streak day by day"},
    {"name": "Daily Habit Tracker", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Track up to 5 daily habits with a heatmap calendar and streak counter"},
    {"name": "Personal Flashcard App", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Create and revise flashcards using spaced repetition for exams and language learning"},
    {"name": "Sleep Quality Logger", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Log bedtime, wake time, and sleep quality; surfaces weekly patterns to improve rest"},
    {"name": "Unit Converter for India", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Converts currency, weight, area (bigha/acre), temperature, and cooking units offline"},
    {"name": "Wedding Checklist App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Month-by-month wedding planning checklist covering venues, vendors, and rituals"},
    {"name": "Packing List Builder", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Build and save reusable packing lists for trips by destination, duration, and season"},
    {"name": "Children's Chore Tracker", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Parents assign daily chores to kids; kids check off tasks to earn star rewards"},
    {"name": "Pet Care Reminder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Tracks vet visits, vaccinations, feeding schedules, and grooming for your pets"},
    {"name": "Daily News Digest — India", "domain": "Media", "type": "Non-Technical",
     "tagline": "Five must-read India news stories delivered every morning in under 3 minutes"},
    {"name": "Podcast Episode Tracker", "domain": "Media", "type": "Non-Technical",
     "tagline": "Log episodes listened, mark favourites, and queue up next episodes across any podcast app"},
    {"name": "Gratitude Journal", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Write three things you're grateful for daily; weekly review surfaces positive patterns"},
    {"name": "Savings Goal Jar", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Set a savings goal, log manual deposits, and see a visual progress bar fill up"},
    {"name": "Time Zone World Clock", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Add cities you work with and see their local times side-by-side — zero setup"},
    {"name": "Festival & Events Calendar — India", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Regional festivals, melas, and cultural events by state with date, significance, and traditions"},
    {"name": "Bus Route & Schedule Finder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Offline-capable KSRTC/MSRTC bus route lookup with schedules and stops — no account needed"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 2 — Days 31–60 — Consumer apps with a payment layer or basic social loop
    # One revenue moment (subscription / tip / split) + simple user accounts
    # Complexity score: 2  |  Good for: monetisation basics, retention, UX flows
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Expense Splitter for Friends", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Split bills in a group, track who owes whom, and settle via UPI with one tap"},
    {"name": "Personal Budget Tracker with UPI Sync", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Auto-categorises UPI transactions from SMS to build a monthly budget dashboard"},
    {"name": "Shared Shopping List", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Household members share a live grocery list — tick items off in real time at the store"},
    {"name": "Digital Business Card", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Create a scannable QR business card; track who viewed or saved your contact"},
    {"name": "Tip Calculator & Bill Splitter", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Enter bill amount, choose tip %, split among any number of people — UPI deep link to pay"},
    {"name": "Menstrual Cycle & PCOS Tracker", "domain": "FemTech", "type": "Non-Technical",
     "tagline": "Tracks cycles, symptoms, and provides personalised diet and exercise tips for PCOS"},
    {"name": "Volunteer Sign-Up App", "domain": "Social Impact", "type": "Non-Technical",
     "tagline": "NGOs post volunteer slots; users discover opportunities near them and RSVP in one tap"},
    {"name": "Children's Screen Time Manager", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Parents set daily app limits and bedtime locks on children's Android devices remotely"},
    {"name": "Local Events Discovery App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Discover free and paid events — comedy shows, workshops, markets — near you this week"},
    {"name": "Freelancer Time Tracker & Invoice Generator", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Log hours per project and auto-generate a GST-ready PDF invoice for clients"},
    {"name": "Meal Planner & Weekly Grocery List", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Plan 7-day meals, auto-generate an ingredient list, and share with a family member"},
    {"name": "Daily Mood Check-In with Therapist Matching", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "30-second daily mood log; when you've had a rough week, get matched to an online therapist"},
    {"name": "Language Exchange Partner Finder", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Match with a native speaker to practise your target language via text and voice chat"},
    {"name": "Gift Registry App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Create wish lists for birthdays, weddings, and baby showers; share link for guests to claim gifts"},
    {"name": "UPI Tip Jar for Creators", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Creators share a link; fans send appreciation payments via UPI with a personalised note"},
    {"name": "Carpool Finder for Office Commuters", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Match colleagues or neighbours going to the same office area; split fuel via UPI"},
    {"name": "Second-Hand Textbook Exchange", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Students list and buy used textbooks within their college campus; meet and pay in person"},
    {"name": "Neighbourhood Safety Alert App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Residents post safety alerts; local police RWA admins verify and broadcast to the colony"},
    {"name": "Building Visitor Log", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Gate security logs visitors digitally; residents approve entries from their phone"},
    {"name": "Classroom Homework Tracker for Schools", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Teachers post homework; students and parents see it in a shared calendar — no WhatsApp groups"},
    {"name": "Fitness Challenge Group App", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Friends create step or workout challenges; daily leaderboard keeps everyone accountable"},
    {"name": "Personal Invoice Manager for Consultants", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Create, send, and track GST invoices; get notified when a client views or pays"},
    {"name": "Pet Adoption Finder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Browse adoptable pets from verified shelters and rescues near your city"},
    {"name": "Community Noticeboard App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Apartment societies post notices, polls, and lost-and-found — replaces 10 WhatsApp groups"},
    {"name": "Pregnancy Week-by-Week Tracker", "domain": "FemTech", "type": "Non-Technical",
     "tagline": "Week-by-week fetal development, symptom tracker, and doctor visit reminders for new mothers"},
    {"name": "Wedding Budget Planner", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Track wedding spend by category against a set budget; share view access with partner"},
    {"name": "Amateur Sports Score Tracker", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Log scores for local cricket, football, or kabaddi tournaments and share a live leaderboard"},
    {"name": "School Carpool Coordinator", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Parents on the same school route take turns driving; automated rotation schedule via app"},
    {"name": "Micro-Newsletter Platform for India", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Launch a paid newsletter in 5 minutes with UPI subscription payments and subscriber analytics"},
    {"name": "Personal Finance Goal Tracker", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Set savings milestones (emergency fund, vacation, gadget) and log progress with visual charts"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 3 — Days 61–90 — Simple two-sided marketplaces
    # Supply + demand matching, basic trust (ratings), no complex payments
    # Complexity score: 3  |  Good for: marketplace dynamics, liquidity, chicken-and-egg problem
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Tiffin Service Marketplace", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Connects home cooks offering daily tiffin with office workers and students nearby"},
    {"name": "Home Tutors Marketplace", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Parents find and book verified local tutors for subjects and boards; pay per session"},
    {"name": "Freelance Errand Runner", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Busy professionals hire local helpers for queue-standing, parcel delivery, and errands"},
    {"name": "Home Cleaning Service Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Book verified home cleaners by the hour; ratings and repeat booking built in"},
    {"name": "Yoga Instructor Finder", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Discover certified yoga and fitness instructors for home, studio, or online sessions"},
    {"name": "Pet Sitter & Dog Walker Marketplace", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Book trusted pet sitters and dog walkers near you with photo updates during the visit"},
    {"name": "Local Produce Marketplace", "domain": "AgriTech", "type": "Non-Technical",
     "tagline": "Farmers list surplus vegetables and fruits; city buyers order for next-day delivery"},
    {"name": "Handmade Crafts Marketplace", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Artisans and home-based crafters sell handmade products directly to Indian buyers"},
    {"name": "Short-Term Tool Rental", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Borrow drills, ladders, and power tools from neighbours for a day at a fraction of purchase cost"},
    {"name": "Home Baker Marketplace", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Order custom cakes, cookies, and desserts from FSSAI-registered home bakers nearby"},
    {"name": "Language Tutor Marketplace", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Book 1:1 sessions with verified native language tutors for 12 Indian and foreign languages"},
    {"name": "Wedding Vendor Directory & Booking", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Browse photographers, caterers, decorators, and mehendi artists by city with verified reviews"},
    {"name": "Event Equipment Rental Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Rent chairs, tables, PA systems, and tents for parties directly from local suppliers"},
    {"name": "Furniture Upcycling Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Carpenters list upcycled and restored furniture; buyers get unique pieces at lower cost"},
    {"name": "Local Tour Guide Marketplace", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Tourists book verified local guides for city walks, food trails, and heritage tours"},
    {"name": "Music Lesson Marketplace", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Find and book music teachers for guitar, tabla, violin, and vocals — online or at home"},
    {"name": "Personal Chef Marketplace", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Hire a trained cook for dinner parties, meal prep, or festive occasions at home"},
    {"name": "Fashion Tailor Finder", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Discover tailors by specialisation (bridal, daily wear, alterations) with turnaround times"},
    {"name": "Catering for Small Events Marketplace", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Order catering for 20–200 guests from local caterers with transparent per-plate pricing"},
    {"name": "Elderly Care Companion Marketplace", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Families hire trained companions for elderly parents for daytime assistance and hospital visits"},
    {"name": "Babysitter Marketplace", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Parents find and book verified babysitters for evenings and weekends with background checks"},
    {"name": "Bicycle Rental Network", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Rent bicycles by the hour from a network of local shops with GPS tracking and UPI payment"},
    {"name": "Freelance Photographer Directory", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Book event, portrait, and product photographers with portfolio reviews and instant quotes"},
    {"name": "Secondhand Electronics Exchange", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Buy and sell used phones, laptops, and gadgets with seller ID verification and buyer ratings"},
    {"name": "House Painter Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Get quotes from verified painters, choose a package, and book with partial advance online"},
    {"name": "Home Repair on Demand", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Book plumbers, electricians, and carpenters with upfront pricing and real-time tracking"},
    {"name": "Organic Produce Subscription Box", "domain": "AgriTech", "type": "Non-Technical",
     "tagline": "Weekly box of certified organic vegetables sourced from local farms near your city"},
    {"name": "Driving Instructor Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Book certified driving instructors for car or two-wheeler lessons at your convenience"},
    {"name": "Homeopathy & Ayurveda Consultation Marketplace", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Book online consultations with verified AYUSH practitioners for chronic and lifestyle conditions"},
    {"name": "Freelance Interior Decorator Marketplace", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Hire interior designers for room makeovers with 3D previews and transparent item costing"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 4 — Days 91–120 — SaaS tools for SMBs & professionals
    # Multi-role users, operational workflows, recurring subscription, reports
    # Complexity score: 4  |  Good for: persona depth, workflow mapping, B2B pricing
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Salon Appointment Booking SaaS", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Walk-in and online appointment management for salons with staff scheduling and revenue reports"},
    {"name": "Gym Membership Management SaaS", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Member check-in, fee collection, batch scheduling, and renewals for independent gyms"},
    {"name": "Clinic OPD Management System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Appointment booking, patient records, prescription generation, and billing for solo clinics"},
    {"name": "School Fee Collection Portal", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Automates fee schedules, payment reminders, receipts, and defaulter tracking for schools"},
    {"name": "Legal Invoice & Matter Tracker for Lawyers", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "Track client matters, billable hours, and generate professional invoices for solo advocates"},
    {"name": "NGO Donor Management System", "domain": "Social Impact", "type": "Non-Technical",
     "tagline": "Track donors, generate 80G receipts, and manage recurring donation mandates for small NGOs"},
    {"name": "Event Planner Project Tracker", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Manage vendors, timelines, budgets, and client approvals for weddings and corporate events"},
    {"name": "Photography Studio Booking & CRM", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Photographers manage shoots, client galleries, payment milestones, and delivery deadlines"},
    {"name": "Tuition Centre Batch Manager", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Manage batches, attendance, fees, and parent communication for coaching institutes"},
    {"name": "Dental Clinic Appointment & Records System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Appointment scheduling, treatment charting, payment tracking, and recall reminders for dentists"},
    {"name": "Pathology Lab Report Manager", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Digitise test orders, auto-generate PDF reports, and send results to patients via WhatsApp"},
    {"name": "Small Hotel Property Management System", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Manage room inventory, check-ins, housekeeping, and billing for independent hotels under 50 rooms"},
    {"name": "Driving School Fleet & Batch Manager", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Schedule lessons, track instructor hours, manage vehicle maintenance, and collect fees"},
    {"name": "Pest Control Service Scheduler", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Manage residential and commercial pest control bookings, technician routes, and chemical inventory"},
    {"name": "Security Agency Staff Tracker", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Guards clock in via app; supervisors track shift attendance, duty locations, and incidents"},
    {"name": "Automobile Workshop Job Card System", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Mechanics create digital job cards, track parts used, and send customer bill estimates on WhatsApp"},
    {"name": "Laundry Management App", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Track garments in, out, and washed; print tags, send ready SMS, and collect payment digitally"},
    {"name": "Library Management System", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Catalogue books, manage member borrowing, track overdue returns, and send SMS reminders"},
    {"name": "Crèche & Daycare Management", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Track child attendance, meals, nap logs, and send daily photo updates to parents"},
    {"name": "Kirana Store Billing & Inventory", "domain": "RetailTech", "type": "Non-Technical",
     "tagline": "Barcode billing, stock alerts, khata credit, and daily sales summary for neighbourhood stores"},
    {"name": "CA Client Document Portal", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "CAs collect documents from clients, track filing deadlines, and share reports securely"},
    {"name": "Coaching Institute Performance Analytics", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Track student mock test scores, identify weak topics, and generate parent progress reports"},
    {"name": "Real Estate Broker CRM", "domain": "PropTech", "type": "Non-Technical",
     "tagline": "Manage property listings, client follow-ups, site visits, and deal pipeline for brokers"},
    {"name": "Recruitment Agency Applicant Tracker", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Manage job requirements, candidate pipeline stages, interview schedules, and client updates"},
    {"name": "Optical Store Management System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Prescription records, frame inventory, order tracking, and recall reminders for optical stores"},
    {"name": "Printing Press Order Management", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Digital job orders, proof approvals, delivery tracking, and invoice generation for print shops"},
    {"name": "Social Media Scheduler for SMBs", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Schedule posts to Instagram, Facebook, and LinkedIn; track basic engagement analytics"},
    {"name": "Freelancer Proposal Generator", "domain": "Productivity", "type": "Non-Technical",
     "tagline": "Templated project proposals with scope, timeline, and pricing that clients e-sign online"},
    {"name": "Mutual Fund Distributor CRM", "domain": "WealthTech", "type": "Non-Technical",
     "tagline": "Track AUM, SIP mandates, client portfolios, and compliance documents for MFD businesses"},
    {"name": "Food Truck Daily Sales Tracker", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Log daily sales, costs, and location; weekly P&L report for food truck entrepreneurs"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 5 — Days 121–150 — Social & community platforms with network effects
    # UGC, viral loops, moderation, engagement mechanics, retention
    # Complexity score: 5  |  Good for: network effects, engagement, community building
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Alumni Network for Indian Colleges", "domain": "Social", "type": "Non-Technical",
     "tagline": "Connects college alumni for mentorship, job referrals, and reunions with verified profiles"},
    {"name": "Neighbourhood Social Network", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Hyperlocal social app for your colony — classified ads, events, service reviews, safety alerts"},
    {"name": "Professional Women's Community for India", "domain": "Social", "type": "Non-Technical",
     "tagline": "Safe professional network for women — mentorship, job referrals, and peer support circles"},
    {"name": "Startup Founder Network India", "domain": "Social", "type": "Non-Technical",
     "tagline": "Verified founder community for co-founder search, investor intros, and deal-sharing"},
    {"name": "Farmer Community Knowledge Exchange", "domain": "AgriTech", "type": "Non-Technical",
     "tagline": "Farmers share crop tips, pest alerts, and market news in regional languages via voice and text"},
    {"name": "Gaming Clan Finder for Indian Gamers", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "BGMI and Free Fire players find and join clans, organise scrims, and track team performance"},
    {"name": "Indie Musician Fan Community", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Independent artists build fan communities, release music, and earn via fan memberships"},
    {"name": "Book Club Social App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Find or create local and online book clubs; share reads, polls, and monthly meeting notes"},
    {"name": "Personal Finance Community India", "domain": "Personal Finance", "type": "Non-Technical",
     "tagline": "Peer Q&A, portfolio sharing, and expert AMAs on investing, FIRE, and money management"},
    {"name": "Open Source Contributor Network India", "domain": "DevTools", "type": "Technical",
     "tagline": "Indian OSS contributors find projects to contribute to, showcase work, and find collaborators"},
    {"name": "Career Mentorship Community", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Professionals offer 30-min mentorship sessions; mentees book and leave public reviews"},
    {"name": "Running & Cycling Club Finder", "domain": "Wellness", "type": "Non-Technical",
     "tagline": "Discover and join local running and cycling groups; plan group rides and share Strava logs"},
    {"name": "Local Artists Showcase Network", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Visual artists and photographers showcase portfolios, receive commissions, and build a following"},
    {"name": "Night Sky & Astronomy Club App", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Amateur astronomers find observing events, share astrophotography, and mentor beginners"},
    {"name": "Board Game Meetup Finder", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Find or host board game sessions near you; community-rated café and club listings"},
    {"name": "Expat Support Community India", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Foreign nationals living in India share local tips, find community events, and get legal advice"},
    {"name": "Stand-Up Comedy Open Mic Finder", "domain": "Media", "type": "Non-Technical",
     "tagline": "Comedians find open mic slots; audiences discover upcoming shows; producers spot talent"},
    {"name": "Street Food Explorer Community", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Foodies review street food stalls by locality; hidden gems surface via community upvotes"},
    {"name": "Devotional & Prayer Group Platform", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Create and join virtual satsangs, bhajan groups, and Quran study circles with attendance tracking"},
    {"name": "Homemaker Skill-Share Community", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Homemakers teach skills like pickling, embroidery, and cooking via short recorded classes"},
    {"name": "Tech Women Mentorship Network", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Women in tech get matched with senior mentors; structured 3-month mentorship programme"},
    {"name": "Birding & Wildlife Enthusiast Network", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Indian birders log sightings, share locations, and build regional species checklists together"},
    {"name": "Student Debate & MUN Community", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Students find MUN conferences, debate tournaments, and practice partners across India"},
    {"name": "Improv & Theatre Community App", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Theatre enthusiasts find workshops, auditions, and amateur production teams in their city"},
    {"name": "Philosophy Discussion Forum India", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Curated discussion threads on Indian and Western philosophy with structured debate norms"},
    {"name": "LGBTQ+ Safe Social Space India", "domain": "Social", "type": "Non-Technical",
     "tagline": "Private, verified community for India's LGBTQ+ population — events, support, and resources"},
    {"name": "Pet Owner Community", "domain": "Consumer", "type": "Non-Technical",
     "tagline": "Pet parents share tips, find local vets and groomers, and organise pet playdates"},
    {"name": "Co-Founder Matching Platform", "domain": "Social", "type": "Non-Technical",
     "tagline": "Entrepreneurs with complementary skills find each other with structured compatibility profiles"},
    {"name": "Special Needs Parent Support Community", "domain": "Social", "type": "Non-Technical",
     "tagline": "Parents of children with special needs share resources, therapist reviews, and emotional support"},
    {"name": "Independent Researcher Network India", "domain": "Social", "type": "Non-Technical",
     "tagline": "Academics and independent researchers collaborate on papers, find peer reviewers, and share grants"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 6 — Days 151–180 — Intermediate multi-feature B2B SaaS
    # Role-based access, integrations, dashboards, compliance basics
    # Complexity score: 6  |  Good for: product architecture, integrations, B2B sales
    # ════════════════════════════════════════════════════════════════════════
    {"name": "HR Leave & Payroll for SMBs", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Leave management, attendance, and monthly payslip generation for companies under 200 employees"},
    {"name": "Construction Project Management App", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Track site progress, labour attendance, material costs, and client billing milestones"},
    {"name": "School ERP Lite", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Admissions, timetable, attendance, fees, and parent communication for K-12 schools"},
    {"name": "Hospital OPD & Billing System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Multi-doctor OPD scheduling, prescription management, lab order integration, and ward billing"},
    {"name": "Restaurant POS with Inventory Management", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Table management, KOT system, recipe-based inventory deduction, and daily sales reports"},
    {"name": "Hotel Channel Manager", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Sync room availability and rates across Booking.com, MakeMyTrip, and Airbnb in real time"},
    {"name": "CA Practice Management Software", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Manage client engagements, filing deadlines, team tasks, billing, and document vaults for CA firms"},
    {"name": "Law Firm Matter & Billing Management", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "Manage client matters, court dates, document drafts, billable hours, and retainer invoices"},
    {"name": "Advertising Agency Project Management", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Brief-to-delivery workflow with client approval stages, resource allocation, and budget tracking"},
    {"name": "Insurance Agency Management System", "domain": "InsurTech", "type": "Non-Technical",
     "tagline": "Track policies, renewals, commissions, and client servicing for POSP and agent businesses"},
    {"name": "Financial Advisor Client Portal", "domain": "WealthTech", "type": "Non-Technical",
     "tagline": "Clients view portfolio, get meeting notes, sign mandates, and upload tax docs in one place"},
    {"name": "Export-Import Documentation Manager", "domain": "B2B Marketplace", "type": "Non-Technical",
     "tagline": "Manage shipping bills, LC documents, and DGFT filings with customs broker collaboration"},
    {"name": "Field Sales Force Automation", "domain": "SaaS", "type": "Technical",
     "tagline": "Sales reps log visits, place orders, and track targets on mobile; managers see live maps"},
    {"name": "Manufacturer Quality Inspection App", "domain": "SaaS", "type": "Technical",
     "tagline": "QC inspectors do digital checklists on the shop floor; defect trends surface in a dashboard"},
    {"name": "Factory Maintenance Management (CMMS Lite)", "domain": "SaaS", "type": "Technical",
     "tagline": "Log machine breakdowns, assign maintenance tasks, and track spare parts inventory for factories"},
    {"name": "Pharmacy Inventory & Billing System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Barcode-based billing, expiry tracking, purchase orders, and schedule H drug compliance"},
    {"name": "Logistics Fleet Management Platform", "domain": "Logistics", "type": "Technical",
     "tagline": "Track truck location, trip history, driver documents, fuel expenses, and customer PODs"},
    {"name": "Diagnostic Centre Management System", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Patient registration, test workflow, result delivery, and NABL compliance reporting"},
    {"name": "Garment Factory Production Tracker", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Line-wise production targets vs actuals, worker efficiency, and buyer-wise order status"},
    {"name": "NGO Project & Grant Tracker", "domain": "Social Impact", "type": "Non-Technical",
     "tagline": "Manage multiple grants, activity budgets, beneficiary data, and donor impact reports"},
    {"name": "Debt Collection Management SaaS", "domain": "Fintech", "type": "Technical",
     "tagline": "Manage recovery queues, agent call logs, legal notices, and settlement tracking for NBFCs"},
    {"name": "B2B Loyalty Programme Platform", "domain": "RetailTech", "type": "Non-Technical",
     "tagline": "Brands run points-based loyalty programmes for their distributor and retailer networks"},
    {"name": "Automobile Dealership CRM & DMS", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Test drive management, booking pipeline, accessories sales, and service history for car dealers"},
    {"name": "Chemical Raw Materials Exchange", "domain": "B2B Marketplace", "type": "Non-Technical",
     "tagline": "Manufacturers list specialty chemicals; buyers get verified lab test reports before purchase"},
    {"name": "Healthcare Equipment Rental Marketplace", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Hospitals rent ICU equipment and diagnostic tools by the day with delivery and calibration"},
    {"name": "Interior Design Project Management", "domain": "SaaS", "type": "Non-Technical",
     "tagline": "Designers share mood boards, track BOQs, vendor deliveries, and client payment milestones"},
    {"name": "Commercial Real Estate Listing Platform", "domain": "PropTech", "type": "Non-Technical",
     "tagline": "Verified commercial office, warehouse, and retail listings with virtual tours and broker leads"},
    {"name": "Solar Panel Installation Management", "domain": "CleanTech", "type": "Non-Technical",
     "tagline": "Solar companies manage site surveys, panel orders, installations, and subsidy applications"},
    {"name": "College Placement Management System", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Placement officers manage company visits, student applications, and offer letter records"},
    {"name": "Industrial Spare Parts Marketplace", "domain": "B2B Marketplace", "type": "Non-Technical",
     "tagline": "OEMs and authorised distributors list spare parts; buyers get guaranteed delivery SLAs"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 7 — Days 181–210 — Technical products with rule-based or basic AI/ML
    # Algorithms, data pipelines, basic models, APIs, integrations
    # Complexity score: 7  |  Good for: data strategy, ML product thinking, API design
    # ════════════════════════════════════════════════════════════════════════
    {"name": "AI Job Description Writer", "domain": "HRTech", "type": "Technical",
     "tagline": "Generates bias-free, SEO-optimised JDs from a bullet-point brief in under 30 seconds"},
    {"name": "OCR for Aadhaar & PAN Extraction", "domain": "Fintech", "type": "Technical",
     "tagline": "Extracts structured data from Aadhaar and PAN card images for KYC auto-fill"},
    {"name": "Spam Filter for Indian SMS & Calls", "domain": "Consumer", "type": "Technical",
     "tagline": "ML model trained on Indian spam patterns blocks scam calls and OTP-phishing messages"},
    {"name": "AI Resume Screener", "domain": "HRTech", "type": "Technical",
     "tagline": "Ranks resumes against job criteria and flags top candidates with match reasoning"},
    {"name": "Sentiment Analysis for Customer Reviews", "domain": "SaaS", "type": "Technical",
     "tagline": "Analyses product and service reviews across platforms to surface actionable sentiment themes"},
    {"name": "Crop Disease Detection from Photos", "domain": "AgriTech", "type": "Technical",
     "tagline": "Farmer takes a photo of an infected leaf; ML model identifies disease and suggests treatment"},
    {"name": "Price Prediction for Second-Hand Cars", "domain": "Marketplace", "type": "Technical",
     "tagline": "Estimates fair market value of used cars using model, age, mileage, and condition data"},
    {"name": "Demand Forecasting for Kirana Stores", "domain": "RetailTech", "type": "Technical",
     "tagline": "Predicts which SKUs a kirana store should stock more of based on local and seasonal demand"},
    {"name": "Document Classification for Legal Firms", "domain": "LegalTech", "type": "Technical",
     "tagline": "Automatically sorts uploaded legal documents into categories and extracts key clause data"},
    {"name": "Smart Energy Consumption Analyser", "domain": "CleanTech", "type": "Technical",
     "tagline": "Analyses smart meter data to identify energy-wasting appliances and suggest off-peak usage"},
    {"name": "Phishing URL Detector", "domain": "SaaS", "type": "Technical",
     "tagline": "API that scores any URL for phishing risk using domain age, WHOIS, and page content signals"},
    {"name": "AI-Powered Quiz Generator for Teachers", "domain": "EdTech", "type": "Technical",
     "tagline": "Teachers paste a chapter text; AI generates multiple-choice and short-answer quiz questions"},
    {"name": "Speech-to-Text for Hindi & Regional Languages", "domain": "AI Tools", "type": "Technical",
     "tagline": "Accurate transcription for Hindi, Tamil, Bengali, and Marathi audio — API and mobile SDK"},
    {"name": "AI Workout Plan Generator", "domain": "Wellness", "type": "Technical",
     "tagline": "Generates personalised weekly workout plans based on goal, fitness level, and available equipment"},
    {"name": "Retail Shelf Audit with Object Detection", "domain": "RetailTech", "type": "Technical",
     "tagline": "Store staff photograph shelves; AI detects out-of-stock, misplaced, and promotional compliance"},
    {"name": "AI Product Description Writer for eCommerce", "domain": "RetailTech", "type": "Technical",
     "tagline": "Sellers upload product specs; AI generates platform-optimised listings for Amazon and Flipkart"},
    {"name": "Fake News Detection Tool", "domain": "Media", "type": "Technical",
     "tagline": "Rates the credibility of viral news articles using source history and claim cross-checking"},
    {"name": "Automated Financial Statement Analysis", "domain": "Fintech", "type": "Technical",
     "tagline": "Extracts ratios, flags anomalies, and summarises P&L, balance sheet, and cash flow from PDFs"},
    {"name": "Recipe Recommendation Engine", "domain": "Consumer", "type": "Technical",
     "tagline": "Users input available ingredients; ML suggests recipes that minimise food waste"},
    {"name": "AI Outreach Message Personaliser for Sales", "domain": "SaaS", "type": "Technical",
     "tagline": "Generates personalised cold emails and LinkedIn messages using prospect's profile and company data"},
    {"name": "Budget Forecast Tool for Startups", "domain": "Fintech", "type": "Technical",
     "tagline": "Founders input revenue and cost assumptions; tool models 12-month P&L and cash runway scenarios"},
    {"name": "Skin Condition Pre-Screening App", "domain": "HealthTech", "type": "Technical",
     "tagline": "User photographs a skin concern; ML model flags severity and recommends dermatologist or OTC"},
    {"name": "Real-Time Video Translation for Meetings", "domain": "AI Tools", "type": "Technical",
     "tagline": "Subtitles video calls in the listener's preferred Indian language with under 2 seconds latency"},
    {"name": "Last-Mile Route Optimisation Engine", "domain": "Logistics", "type": "Technical",
     "tagline": "Optimises delivery routes for hyperlocal fleets using real-time traffic and priority windows"},
    {"name": "AI Scriptwriter for Reels & Short Video", "domain": "Creator", "type": "Technical",
     "tagline": "Generates short-form video scripts with hook, value, and CTA tailored to creator's niche"},
    {"name": "Chatbot Builder for SMB Customer Support", "domain": "SaaS", "type": "Technical",
     "tagline": "No-code FAQ chatbot for WhatsApp and websites using uploaded product docs and FAQs"},
    {"name": "Mandi Price Prediction for Farmers", "domain": "AgriTech", "type": "Technical",
     "tagline": "Predicts wholesale crop prices for the next 14 days based on arrival, weather, and market data"},
    {"name": "Auto-Tagging for eCommerce Product Photos", "domain": "RetailTech", "type": "Technical",
     "tagline": "Computer vision model generates attribute tags (colour, style, fabric) for fashion product images"},
    {"name": "Predictive Churn Alert for SaaS Products", "domain": "SaaS", "type": "Technical",
     "tagline": "Identifies accounts at risk of churning by analysing login frequency, feature usage, and support tickets"},
    {"name": "Sign Language to Text Converter", "domain": "Social Impact", "type": "Technical",
     "tagline": "Camera-based ISL interpreter that converts hand gestures to text for Deaf communication"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 8 — Days 211–240 — Complex multi-sided platforms with trust & verification
    # Identity, escrow, dispute resolution, regulatory onboarding, fraud
    # Complexity score: 8  |  Good for: trust & safety, regulatory, platform governance
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Background Verification SaaS", "domain": "HRTech", "type": "Technical",
     "tagline": "API-driven BGV for employment history, education, criminal records, and reference checks"},
    {"name": "Certified Pre-Owned Car Platform", "domain": "Marketplace", "type": "Technical",
     "tagline": "Multi-point inspection, RC transfer facilitation, and financing for used car transactions"},
    {"name": "Freelancer Escrow & Milestone Payments", "domain": "Fintech", "type": "Technical",
     "tagline": "Funds held in escrow; released to freelancer on client milestone approval with dispute resolution"},
    {"name": "Domestic Worker Placement & Verification", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "Verified maids, cooks, and drivers with police verification, training, and background check"},
    {"name": "Verified Organic Produce Marketplace", "domain": "AgriTech", "type": "Technical",
     "tagline": "Third-party certified organic farms list produce; traceability QR codes on every packet"},
    {"name": "Licensed Contractor Marketplace", "domain": "Marketplace", "type": "Technical",
     "tagline": "Civil and MEP contractors bid on residential projects; escrow-based payment milestone system"},
    {"name": "Peer Lending with Credit Verification", "domain": "WealthTech", "type": "Technical",
     "tagline": "RBI-compliant P2P platform with bureau pull, risk-tiered interest rates, and escrow collections"},
    {"name": "Certified Pre-Owned Electronics Marketplace", "domain": "Marketplace", "type": "Technical",
     "tagline": "All devices tested by certified repair centres; graded condition reports and 6-month warranty"},
    {"name": "Second-Hand Luxury Goods Authentication", "domain": "Marketplace", "type": "Technical",
     "tagline": "Physical authentication of luxury bags, watches, and jewellery before listing and after sale"},
    {"name": "Verified Co-Living Spaces Platform", "domain": "PropTech", "type": "Non-Technical",
     "tagline": "Student and professional accommodation with verified landlord KYC, digital agreements, and reviews"},
    {"name": "Healthcare Professional Credential Verifier", "domain": "HealthTech", "type": "Technical",
     "tagline": "Verifies doctor registrations against NMC and state council databases for telemedicine platforms"},
    {"name": "Licensed Financial Advisor Finder", "domain": "WealthTech", "type": "Technical",
     "tagline": "Investors find SEBI-registered RIAs by fee model, specialisation, and verified track record"},
    {"name": "Certified Nutritionist Consultation Platform", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Certified dieticians offer subscription-based consultations with meal plan management tools"},
    {"name": "Accredited Online Certification Platform", "domain": "EdTech", "type": "Technical",
     "tagline": "Issues blockchain-verified certificates for online courses with employer verification portal"},
    {"name": "Verified Legal Aid Network", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "Bar Council-verified lawyers offer free and subsidised legal aid to underserved clients"},
    {"name": "Vehicle Inspection Marketplace", "domain": "Marketplace", "type": "Technical",
     "tagline": "Car owners book standardised 150-point inspections from certified engineers before selling"},
    {"name": "Special Needs Tutor Network", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "RCI-certified special educators are matched to children with disabilities for home therapy"},
    {"name": "Licensed Architect Finder", "domain": "PropTech", "type": "Non-Technical",
     "tagline": "COA-registered architects are matched to home construction and renovation projects with escrow"},
    {"name": "Professional Mover & Packer Verification", "domain": "Marketplace", "type": "Non-Technical",
     "tagline": "GST-registered packers and movers with GPS-tracked vehicles and insurance for goods in transit"},
    {"name": "Certified Solar Installer Marketplace", "domain": "CleanTech", "type": "Technical",
     "tagline": "MNRE-empanelled solar vendors bid on rooftop projects; subsidy application handled end-to-end"},
    {"name": "Used Medical Equipment Marketplace", "domain": "HealthTech", "type": "Technical",
     "tagline": "Hospitals sell surplus equipment; buyers get calibration certificates and service history"},
    {"name": "Rental Agreement Automation", "domain": "PropTech", "type": "Technical",
     "tagline": "Digitally drafted, legally valid rental agreements with e-sign and auto-registration in under 10 min"},
    {"name": "Caregiver Matching Platform for Senior Citizens", "domain": "HealthTech", "type": "Non-Technical",
     "tagline": "Background-verified caregivers matched to elderly patients; health log shared with families"},
    {"name": "Verified FSSAI-Compliant Food Supplier Directory", "domain": "FoodTech", "type": "Technical",
     "tagline": "Restaurants find verified ingredient suppliers with active FSSAI licenses and lab test reports"},
    {"name": "Homeschool Curriculum & Tutor Network", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Homeschooling families access structured curriculum, assessment tools, and certified tutors"},
    {"name": "SEBI-Registered Financial Planner Directory", "domain": "WealthTech", "type": "Technical",
     "tagline": "Investors verify RIA credentials, compare fee structures, and book paid consultations"},
    {"name": "Remote Work Job Board for India", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Verified remote-first companies post roles; candidates see WFH policy, salary, and work culture data"},
    {"name": "Divorce & Separation Legal Services Platform", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "Verified lawyers offer fixed-fee divorce proceedings, mediation, and document filing services"},
    {"name": "Import Broker Verified Directory", "domain": "B2B Marketplace", "type": "Non-Technical",
     "tagline": "Importers find CHALR-licensed customs brokers by port, commodity, and verified client reviews"},
    {"name": "Contract Staffing & Compliance Platform", "domain": "HRTech", "type": "Technical",
     "tagline": "Companies hire contract staff through verified staffing agencies; PF/ESI compliance auto-handled"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 9 — Days 241–270 — Complex SaaS with deep integrations & compliance
    # Multi-regulator, ERPs, APIs, audit trails, data residency
    # Complexity score: 9  |  Good for: regulatory product thinking, enterprise sales, data architecture
    # ════════════════════════════════════════════════════════════════════════
    {"name": "GST Filing & Reconciliation SaaS", "domain": "Fintech", "type": "Technical",
     "tagline": "Auto-reconciles GSTR-2A/2B with purchase register; files all GST returns with error validation"},
    {"name": "Payroll Compliance Automation", "domain": "HRTech", "type": "Technical",
     "tagline": "Auto-calculates PF, ESI, PT, and TDS; files statutory returns across all Indian states"},
    {"name": "FSSAI Compliance Management System", "domain": "FoodTech", "type": "Technical",
     "tagline": "Manages FSSAI licenses, audit checklists, recall management, and labelling compliance for F&B brands"},
    {"name": "Environmental Compliance Tracker for Factories", "domain": "CleanTech", "type": "Technical",
     "tagline": "Tracks consent-to-operate conditions, effluent data, and PCB submission deadlines for industries"},
    {"name": "POSH Compliance Management SaaS", "domain": "HRTech", "type": "Technical",
     "tagline": "IC committee management, complaint tracking, inquiry documentation, and annual POSH reporting"},
    {"name": "Healthcare Data Management with ABDM Integration", "domain": "HealthTech", "type": "Technical",
     "tagline": "Creates and manages Ayushman Bharat Health Accounts; links patient records across ABDM-compliant apps"},
    {"name": "RERA Compliance Management for Developers", "domain": "PropTech", "type": "Technical",
     "tagline": "Manages RERA registrations, quarterly progress reports, escrow utilisation, and complaint responses"},
    {"name": "Factory Safety Inspection & Audit SaaS", "domain": "SaaS", "type": "Technical",
     "tagline": "Digital safety checklists, near-miss reporting, hazard mapping, and DGFASLI compliance tracking"},
    {"name": "Labour Law Compliance Management", "domain": "HRTech", "type": "Technical",
     "tagline": "Tracks state-specific labour law obligations — notices, registers, returns, and inspection readiness"},
    {"name": "ISO 9001 Quality Management SaaS", "domain": "SaaS", "type": "Technical",
     "tagline": "Document control, CAPA management, internal audit scheduling, and NCR tracking for ISO-certified firms"},
    {"name": "Income Tax Demand & Appeal Management", "domain": "Fintech", "type": "Technical",
     "tagline": "CAs manage client IT demands, notices, and appeal filings with deadline tracking and document vault"},
    {"name": "E-Way Bill & GST E-Invoicing Automation", "domain": "Fintech", "type": "Technical",
     "tagline": "Generates IRN and e-way bills at scale via ERP integration; auto-cancelled on credit note"},
    {"name": "MSME Credit & Samadhaan Portal Integration", "domain": "Fintech", "type": "Technical",
     "tagline": "Helps MSMEs file delayed payment complaints and track MSME Samadhaan case status automatically"},
    {"name": "Clinical Trial Management with CDSCO Compliance", "domain": "HealthTech", "type": "Technical",
     "tagline": "Manages trial protocols, patient enrolment, SAE reporting, and CDSCO regulatory submissions"},
    {"name": "Building Plan Approval Tracking Platform", "domain": "PropTech", "type": "Technical",
     "tagline": "Architects track plan submissions across municipal bodies with document version control"},
    {"name": "Education NAAC & NIRF Data Management", "domain": "EdTech", "type": "Technical",
     "tagline": "Colleges collect, validate, and submit accreditation data for NAAC and NIRF rankings"},
    {"name": "Pollution Control Board Compliance Tracker", "domain": "CleanTech", "type": "Technical",
     "tagline": "Factories track ambient air and water quality submissions and PCB notices in a single dashboard"},
    {"name": "Telecom DoT Compliance Management", "domain": "SaaS", "type": "Technical",
     "tagline": "Telecom companies track DoT licence conditions, QoS submissions, and audit responses"},
    {"name": "Banking Reconciliation & Ind-AS Reporting", "domain": "Fintech", "type": "Technical",
     "tagline": "Automates Ind-AS 109 expected credit loss calculations and RBI prudential reporting schedules"},
    {"name": "ESG Reporting with BRSR Compliance", "domain": "CleanTech", "type": "Technical",
     "tagline": "Collects Scope 1-3 emissions data and generates SEBI BRSR disclosures for listed companies"},
    {"name": "Export Compliance & DGFT Documentation", "domain": "B2B Marketplace", "type": "Technical",
     "tagline": "Manages SCOMET, AD Code, RoDTEP claims, and DGFT portal submissions for exporters"},
    {"name": "DPDP Act Data Privacy Compliance Tool", "domain": "SaaS", "type": "Technical",
     "tagline": "Maps personal data flows, manages consent records, and automates data principal rights requests"},
    {"name": "Pharmaceutical Drug Approval Tracker", "domain": "HealthTech", "type": "Technical",
     "tagline": "Tracks CDSCO and state drug controller submissions, licences, and inspection schedules"},
    {"name": "FEMA-Compliant Foreign Remittance Management", "domain": "Fintech", "type": "Technical",
     "tagline": "Manages LRS and business remittances with form 15CA/CB, AD bank coordination, and audit trail"},
    {"name": "Chartered Accountant Audit Management", "domain": "Fintech", "type": "Technical",
     "tagline": "Audit planning, working paper management, ICAI standards compliance, and UDIN generation"},
    {"name": "Smart Meter Data Analytics for Utilities", "domain": "CleanTech", "type": "Technical",
     "tagline": "Processes AMI data to detect theft, forecast demand, and enable dynamic tariff management"},
    {"name": "Cold Chain Monitoring & FSSAI Compliance", "domain": "Logistics", "type": "Technical",
     "tagline": "IoT temperature sensors with FSSAI cold chain protocol compliance and batch recall traceability"},
    {"name": "IRDAI-Compliant Insurance Policy Admin System", "domain": "InsurTech", "type": "Technical",
     "tagline": "Manages policy issuance, endorsements, claims, and IRDAI reporting for general insurers"},
    {"name": "Stock Broker SEBI Trade Reporting System", "domain": "WealthTech", "type": "Technical",
     "tagline": "Automates SEBI trade-data file submissions, margin reporting, and obligation settlement"},
    {"name": "Real Estate Fund Accounting with SEBI AIF Rules", "domain": "WealthTech", "type": "Technical",
     "tagline": "NAV calculation, investor reporting, and SEBI Category II AIF compliance for real estate funds"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 10 — Days 271–300 — AI-heavy enterprise products
    # LLMs, ML pipelines, model governance, explainability, large-scale inference
    # Complexity score: 10  |  Good for: AI product strategy, enterprise AI adoption, model lifecycle
    # ════════════════════════════════════════════════════════════════════════
    {"name": "LLM-Powered Credit Appraisal System for Banks", "domain": "Fintech", "type": "Technical",
     "tagline": "Generates Credit Appraisal Memorandums from financials, GST data, and bureau reports in under 1 hour"},
    {"name": "LLM-Driven Contract Risk Analysis", "domain": "LegalTech", "type": "Technical",
     "tagline": "Analyses commercial contracts, flags non-standard clauses, and benchmarks against market norms"},
    {"name": "AI Invoice Processing & 3-Way Matching", "domain": "SaaS", "type": "Technical",
     "tagline": "OCR + LLM extracts invoice data and auto-matches to PO and GRN; flags discrepancies for approval"},
    {"name": "Conversational AI for Bank Customer Service", "domain": "Fintech", "type": "Technical",
     "tagline": "Handles 80% of inbound banking queries via WhatsApp and IVR with context-aware multi-turn dialogue"},
    {"name": "AI-Powered Anti-Money Laundering System", "domain": "Fintech", "type": "Technical",
     "tagline": "Graph-based transaction analysis flags structuring, smurfing, and shell entity patterns for FIU-IND"},
    {"name": "Predictive Maintenance for Industrial Equipment", "domain": "SaaS", "type": "Technical",
     "tagline": "Sensor time-series ML models predict equipment failure 7 days ahead to minimise unplanned downtime"},
    {"name": "AI-Driven Clinical Decision Support System", "domain": "HealthTech", "type": "Technical",
     "tagline": "Surfaces evidence-based treatment protocols and drug interaction alerts at the point of clinical care"},
    {"name": "Enterprise Knowledge Graph for Organisations", "domain": "SaaS", "type": "Technical",
     "tagline": "Connects people, projects, documents, and expertise across the enterprise for intelligent search"},
    {"name": "AI-Powered Demand Planning for FMCG", "domain": "RetailTech", "type": "Technical",
     "tagline": "ML models predict SKU demand by region and channel 12 weeks ahead to optimise production runs"},
    {"name": "Computer Vision Quality Control for Manufacturing", "domain": "SaaS", "type": "Technical",
     "tagline": "High-speed cameras with defect detection models inspect 100% of units on production lines"},
    {"name": "LLM-Powered Legal Research Platform", "domain": "LegalTech", "type": "Technical",
     "tagline": "Searches Indian case law, statutes, and judgments; synthesises relevant precedents with citations"},
    {"name": "AI-Powered Procurement Spend Analytics", "domain": "SaaS", "type": "Technical",
     "tagline": "Classifies unstructured PO and invoice data; identifies maverick spend and preferred-vendor savings"},
    {"name": "Real-Time Personalisation Engine for eCommerce", "domain": "RetailTech", "type": "Technical",
     "tagline": "Serves individualised homepage, search, and email recommendations based on real-time intent signals"},
    {"name": "AI-Driven HR Talent Analytics Platform", "domain": "HRTech", "type": "Technical",
     "tagline": "Predicts attrition risk, identifies high-potential employees, and models workforce skill gaps"},
    {"name": "Intelligent Document Processing for Insurance", "domain": "InsurTech", "type": "Technical",
     "tagline": "Extracts structured data from claim documents, hospital bills, and policy schedules at scale"},
    {"name": "AI-Powered Network Intrusion Detection", "domain": "SaaS", "type": "Technical",
     "tagline": "ML models analyse network traffic in real time to detect APTs and zero-day exploit patterns"},
    {"name": "AI-Driven Supply Chain Risk Monitoring", "domain": "Logistics", "type": "Technical",
     "tagline": "Monitors supplier financials, geopolitical signals, and logistics events to predict disruptions"},
    {"name": "Enterprise Semantic Search Platform", "domain": "SaaS", "type": "Technical",
     "tagline": "Indexes internal wikis, Slack, and email; employees find knowledge via natural language queries"},
    {"name": "AI-Powered Dynamic Pricing Engine", "domain": "RetailTech", "type": "Technical",
     "tagline": "Optimises prices in real time across SKUs, channels, and regions based on demand and competitor data"},
    {"name": "LLM-Based Scientific Literature Review for Pharma", "domain": "HealthTech", "type": "Technical",
     "tagline": "Scans PubMed and clinical trial databases to generate evidence summaries for drug researchers"},
    {"name": "AI-Driven Trade Surveillance for Stock Exchanges", "domain": "WealthTech", "type": "Technical",
     "tagline": "Detects insider trading, front-running, and market manipulation using ML on order book data"},
    {"name": "Automated Underwriting for Commercial Insurance", "domain": "InsurTech", "type": "Technical",
     "tagline": "ML model scores commercial risks using financials, site data, and claims history for quoting"},
    {"name": "AI-Powered Fraud Detection for UPI Networks", "domain": "Fintech", "type": "Technical",
     "tagline": "Graph neural network identifies fraud rings and account takeovers in real-time UPI transaction flows"},
    {"name": "AI-Powered Radiology Imaging Analysis", "domain": "HealthTech", "type": "Technical",
     "tagline": "Deep learning models detect TB, pneumonia, and fractures in X-rays to assist overburdened radiologists"},
    {"name": "Intelligent Contract Lifecycle Management", "domain": "LegalTech", "type": "Technical",
     "tagline": "AI extracts obligations, auto-triggers renewals, and flags deviations from standard playbooks"},
    {"name": "Enterprise Chatbot with Multi-System Orchestration", "domain": "SaaS", "type": "Technical",
     "tagline": "LLM agent answers employee questions by querying HR, ERP, and policy systems in a single turn"},
    {"name": "AI-Driven Carbon Footprint Optimisation", "domain": "CleanTech", "type": "Technical",
     "tagline": "Models supply chain Scope 3 emissions and recommends substitution and logistics changes for reduction"},
    {"name": "Vernacular AI Voice Assistant for Bharat", "domain": "AI Tools", "type": "Technical",
     "tagline": "Voice-first AI assistant in 10 Indian languages for banking, health, and government service queries"},
    {"name": "AI-Powered Agricultural Credit Scoring", "domain": "Fintech", "type": "Technical",
     "tagline": "Scores farmer creditworthiness using satellite imagery, soil data, and crop history for NBFC lending"},
    {"name": "AI Model Governance & Explainability Platform", "domain": "AI Tools", "type": "Technical",
     "tagline": "Tracks model lineage, bias metrics, and drift; generates regulator-ready explainability reports"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 11 — Days 301–330 — Platform / ecosystem / developer-API products
    # Developer adoption, rate limits, pricing by consumption, ecosystem lock-in
    # Complexity score: 11  |  Good for: platform thinking, API design, developer experience
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Communication API Platform (SMS/WhatsApp/Email)", "domain": "DevTools", "type": "Technical",
     "tagline": "Unified API for transactional SMS, WhatsApp Business, and email with delivery analytics and failover"},
    {"name": "KYC & Onboarding API Platform", "domain": "Fintech", "type": "Technical",
     "tagline": "Single API for Aadhaar OTP, PAN, video KYC, and bank account verification with consent management"},
    {"name": "E-Sign API Platform", "domain": "LegalTech", "type": "Technical",
     "tagline": "IT Act-compliant Aadhaar-based e-sign and DSC API for documents, agreements, and forms at scale"},
    {"name": "Payment Reconciliation API for Marketplaces", "domain": "Fintech", "type": "Technical",
     "tagline": "Automates settlement reconciliation across payment gateways, banks, and marketplace payouts"},
    {"name": "Logistics Multi-Carrier API Aggregation", "domain": "Logistics", "type": "Technical",
     "tagline": "Single integration for Delhivery, BlueDart, Ekart, and 15+ carriers with smart rate and SLA routing"},
    {"name": "Tax Calculation API for SaaS Products", "domain": "Fintech", "type": "Technical",
     "tagline": "Real-time GST, TDS, and TCS calculation API for billing systems with state-specific rule engine"},
    {"name": "Embedded Insurance API Platform", "domain": "InsurTech", "type": "Technical",
     "tagline": "Travel, device, and health insurance embedded into third-party apps via a single API integration"},
    {"name": "BNPL API for eCommerce Merchants", "domain": "Fintech", "type": "Technical",
     "tagline": "Plug-and-play BNPL checkout for merchants; credit decisioning and collections handled by platform"},
    {"name": "Account Aggregator Gateway Platform", "domain": "Fintech", "type": "Technical",
     "tagline": "FIP and FIU integration with the RBI Account Aggregator framework for consent-based financial data"},
    {"name": "Vehicle & Driving Licence Verification API", "domain": "SaaS", "type": "Technical",
     "tagline": "Instant RC, DL, and insurance validity checks via VAHAN and SARATHI integration for logistics apps"},
    {"name": "GST Verification & ITC Matching API", "domain": "Fintech", "type": "Technical",
     "tagline": "Validates GSTIN authenticity and auto-reconciles purchase invoices against GSTR-2B for ERP systems"},
    {"name": "ONDC Seller Integration SDK", "domain": "RetailTech", "type": "Technical",
     "tagline": "Helps sellers onboard to ONDC network with catalogue sync, order management, and logistics hooks"},
    {"name": "Identity Verification API with Face Match", "domain": "Fintech", "type": "Technical",
     "tagline": "Matches selfie to Aadhaar photo with liveness detection for secure digital onboarding"},
    {"name": "DigiLocker Integration API", "domain": "SaaS", "type": "Technical",
     "tagline": "Fetches verified government documents (marksheets, licenses, RC) from DigiLocker with user consent"},
    {"name": "Analytics SDK for Mobile Apps", "domain": "DevTools", "type": "Technical",
     "tagline": "Lightweight mobile SDK that captures user events, funnels, and retention cohorts without sampling"},
    {"name": "A/B Testing Platform for Product Teams", "domain": "DevTools", "type": "Technical",
     "tagline": "Feature experimentation with statistical significance calculation and guardrail metric monitoring"},
    {"name": "Feature Flag API for Enterprise", "domain": "DevTools", "type": "Technical",
     "tagline": "Gradual rollouts, kill switches, and targeted feature releases without code deploys"},
    {"name": "Video Calling SDK for Healthcare & EdTech", "domain": "HealthTech", "type": "Technical",
     "tagline": "HIPAA-ready WebRTC SDK with recording, waiting room, and low-bandwidth adaptation for Indian networks"},
    {"name": "Push Notification & In-App Messaging SDK", "domain": "DevTools", "type": "Technical",
     "tagline": "Omnichannel messaging SDK for push, in-app, and SMS with segmentation and delivery analytics"},
    {"name": "Data Enrichment API for Sales Teams", "domain": "SaaS", "type": "Technical",
     "tagline": "Enriches CRM contacts with company firmographics, funding data, and decision-maker signals"},
    {"name": "Open Banking API Aggregator", "domain": "Fintech", "type": "Technical",
     "tagline": "Standardised API layer over Indian banks for balance, transactions, and statement pull with consent"},
    {"name": "Social Login & Fraud Signal API", "domain": "SaaS", "type": "Technical",
     "tagline": "OAuth for Google/Facebook/Jio login plus real-time device and behaviour fraud score for registration"},
    {"name": "Map & Geolocation API for Delivery Apps", "domain": "Logistics", "type": "Technical",
     "tagline": "India-optimised routing, geocoding, and ETAs with offline-capable SDK for last-mile delivery apps"},
    {"name": "Merchant Onboarding API for Payment Aggregators", "domain": "Fintech", "type": "Technical",
     "tagline": "Automates KYC, bank verification, and risk scoring for merchant activation in under 24 hours"},
    {"name": "Observability & APM SDK for Microservices", "domain": "DevTools", "type": "Technical",
     "tagline": "Auto-instruments distributed traces, metrics, and logs across services with anomaly alerting"},
    {"name": "Developer Platform for Credit Bureau Integrations", "domain": "Fintech", "type": "Technical",
     "tagline": "Standardised SDK for CIBIL, Equifax, Experian, and CRIF pulls with consent, caching, and error handling"},
    {"name": "CI/CD Pipeline Health Monitor", "domain": "DevTools", "type": "Technical",
     "tagline": "Tracks build success rates, flaky tests, and deployment frequency with DORA metrics dashboard"},
    {"name": "Incident Post-Mortem & Runbook Assistant", "domain": "DevTools", "type": "Technical",
     "tagline": "AI-assisted post-mortems from alert logs and chat history; runbooks stored and linked to alerts"},
    {"name": "API Gateway for Indian Fintech Stack", "domain": "Fintech", "type": "Technical",
     "tagline": "Unified API management layer over UPI, Aadhaar, GSTN, and bureau with rate limiting and audit logs"},
    {"name": "Marketplace Seller Analytics & Pricing API", "domain": "RetailTech", "type": "Technical",
     "tagline": "Aggregates Amazon, Flipkart, and Meesho seller metrics; competitive price intelligence via API"},

    # ════════════════════════════════════════════════════════════════════════
    # TIER 12 — Days 331–365 — Infrastructure, real-time systems & regulatory-grade tech
    # Sub-millisecond latency, distributed systems, national-scale, cryptography
    # Complexity score: 12  |  Good for: systems thinking, data architecture, national-scale product design
    # ════════════════════════════════════════════════════════════════════════
    {"name": "Real-Time Payments Fraud Prevention Infrastructure", "domain": "Fintech", "type": "Technical",
     "tagline": "Sub-100ms fraud scoring layer for UPI and IMPS with graph neural networks and rule engine fallback"},
    {"name": "Core Banking System for Small Finance Banks", "domain": "Fintech", "type": "Technical",
     "tagline": "Cloud-native CBS with real-time GL, multi-product lending, and RBI reporting for SFBs"},
    {"name": "Distributed Land Registry on Blockchain", "domain": "GovTech", "type": "Technical",
     "tagline": "Immutable property ownership records with consent-based title transfer and charge registry"},
    {"name": "Real-Time Trade Matching Engine for Stock Exchanges", "domain": "WealthTech", "type": "Technical",
     "tagline": "Microsecond-latency order matching with price-time priority, circuit breakers, and MIS reporting"},
    {"name": "CBDC Wallet Infrastructure", "domain": "Fintech", "type": "Technical",
     "tagline": "Retail e-Rupee wallet with offline transaction capability, programmable money, and RBI interoperability"},
    {"name": "Multi-Party Computation for Privacy-Preserving Credit Scoring", "domain": "Fintech", "type": "Technical",
     "tagline": "Banks jointly compute credit scores across datasets without exposing raw customer data to each other"},
    {"name": "Federated Learning Platform for Healthcare AI", "domain": "HealthTech", "type": "Technical",
     "tagline": "Hospitals collaboratively train diagnostic models without sharing patient data across institutions"},
    {"name": "Real-Time Systemic Risk Monitor for RBI", "domain": "Fintech", "type": "Technical",
     "tagline": "Aggregates SLTRO, repo, and payment system data to surface contagion risk in the financial system"},
    {"name": "Satellite-Based Crop Insurance Settlement Engine", "domain": "InsurTech", "type": "Technical",
     "tagline": "Auto-triggers PMFBY payouts using NDVI satellite imagery for yield estimation without field surveys"},
    {"name": "IoT Data Pipeline for Smart City Infrastructure", "domain": "GovTech", "type": "Technical",
     "tagline": "Ingests millions of sensor events per second from traffic, air quality, and utility meters"},
    {"name": "Decentralised Identity for Citizen Services", "domain": "GovTech", "type": "Technical",
     "tagline": "W3C DID-based identity layer enabling citizens to share verifiable credentials across departments"},
    {"name": "Real-Time AML Monitoring for Correspondent Banking", "domain": "Fintech", "type": "Technical",
     "tagline": "Cross-border transaction graph analysis for FATF-compliant AML between correspondent bank networks"},
    {"name": "High-Frequency Trading Infrastructure for Indian Exchanges", "domain": "WealthTech", "type": "Technical",
     "tagline": "Co-location-ready order management and execution system with hardware timestamping and FIX protocol"},
    {"name": "Distributed CDN for Bharat-Scale Video Streaming", "domain": "Media", "type": "Technical",
     "tagline": "Edge network with 2G/3G adaptive bitrate and regional PoPs to serve video to 500M rural users"},
    {"name": "Multi-Cloud Orchestration for Regulated Industries", "domain": "SaaS", "type": "Technical",
     "tagline": "Policy-driven workload placement across AWS, Azure, and GCP with data residency and audit controls"},
    {"name": "Critical Infrastructure SCADA Security Monitor", "domain": "SaaS", "type": "Technical",
     "tagline": "Passive OT network monitoring for power grids and water utilities with anomaly detection and CERT-In reporting"},
    {"name": "Autonomous Vehicle Sensor Fusion Pipeline", "domain": "AI Tools", "type": "Technical",
     "tagline": "Merges LiDAR, camera, and radar data streams for real-time environment perception in AV systems"},
    {"name": "Quantum-Safe Cryptography Migration Toolkit for Banks", "domain": "Fintech", "type": "Technical",
     "tagline": "Audits existing encryption, generates migration plans, and implements NIST post-quantum algorithms"},
    {"name": "Real-Time Electricity Grid Balancing Platform", "domain": "CleanTech", "type": "Technical",
     "tagline": "Matches renewable generation with demand in real time; automated DSM triggers for grid operators"},
    {"name": "Interoperable Health Data Exchange (ABDM Backbone)", "domain": "HealthTech", "type": "Technical",
     "tagline": "FHIR-compliant health information exchange that links EMRs, labs, and pharmacies via ABHA"},
    {"name": "Central Counterparty Clearing Risk Engine", "domain": "WealthTech", "type": "Technical",
     "tagline": "Real-time margin calculation, default waterfall simulation, and stress testing for NSCCL"},
    {"name": "Real-Time Weather Data Pipeline for Parametric Insurance", "domain": "InsurTech", "type": "Technical",
     "tagline": "Ingests IMD and private weather station data; triggers parametric payouts within 24 hours of event"},
    {"name": "Blockchain-Based Trade Finance Platform", "domain": "Fintech", "type": "Technical",
     "tagline": "Banks on a permissioned ledger co-validate LC issuance, document presentation, and payment release"},
    {"name": "Digital Public Infrastructure for Social Benefits", "domain": "GovTech", "type": "Technical",
     "tagline": "Open DPI layer linking Aadhaar, Jan Dhan, and PM-JAY to enable direct benefit transfer at national scale"},
    {"name": "Secure Multi-Party Computation for Tax Data", "domain": "GovTech", "type": "Technical",
     "tagline": "CBDT and state tax authorities compute aggregate analytics across taxpayer data without raw data exposure"},
    {"name": "AI Regulation Compliance Framework Platform", "domain": "AI Tools", "type": "Technical",
     "tagline": "Maps AI system risks to India's proposed AI regulatory requirements and generates conformity assessments"},
    {"name": "Real-Time Carbon Credit Exchange Infrastructure", "domain": "CleanTech", "type": "Technical",
     "tagline": "Registry, matching engine, and settlement system for India's voluntary and compliance carbon markets"},
    {"name": "Digital Twin for Urban Planning", "domain": "GovTech", "type": "Technical",
     "tagline": "3D city model fed by GIS, sensor, and demographic data for infrastructure and zoning decisions"},
    {"name": "Homomorphic Encryption Service for Financial Data Sharing", "domain": "Fintech", "type": "Technical",
     "tagline": "Banks compute analytics on encrypted customer data shared with partners without decryption"},
    {"name": "National-Scale Health Claims Processing Engine", "domain": "HealthTech", "type": "Technical",
     "tagline": "Processes PM-JAY and CGHS claims at 1M+ transactions per day with fraud scoring and auto-adjudication"},
    {"name": "Cross-Border Payment Settlement with FX Hedging", "domain": "Fintech", "type": "Technical",
     "tagline": "Correspondent banking platform with real-time FX hedging and SWIFT/UPI cross-border interoperability"},
    {"name": "Sovereign Data Localisation Compliance Infrastructure", "domain": "SaaS", "type": "Technical",
     "tagline": "Enforces data residency policies for MNCs operating in India with automated cross-border flow detection"},
    {"name": "5G Network Slicing Management for Enterprise", "domain": "SaaS", "type": "Technical",
     "tagline": "Orchestrates dedicated 5G network slices for Industry 4.0 use cases with guaranteed QoS SLAs"},
    {"name": "Satellite Imagery Analytics for Agricultural Credit", "domain": "Fintech", "type": "Technical",
     "tagline": "ISRO/Planet imagery pipeline estimates sown area and crop health for KCC credit underwriting at scale"},
    {"name": "National Credit Registry Infrastructure", "domain": "Fintech", "type": "Technical",
     "tagline": "RBI-mandated centralised credit data repository with real-time bureau reporting and consent framework"},
]


_UNUSED_START = [
    {"name": "_placeholder_", "domain": "", "type": "",
     "tagline": ""},
    {"name": "Freight Marketplace for Truckers", "domain": "Logistics", "type": "Non-Technical",
     "tagline": "Connects SME shippers with verified truckers for full and part truckloads"},

    # ── HR / Recruitment ──
    {"name": "AI Job Description Writer", "domain": "HRTech", "type": "Technical",
     "tagline": "Generates bias-free, optimised JDs from a bullet-point brief in 30 seconds"},
    {"name": "Background Verification SaaS", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Automated BGV for education, employment, and criminal records with API integration"},
    {"name": "Gig Worker Benefits Platform", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Provides portable health insurance, PF, and ESI to gig and contract workers"},

    # ── Real Estate / PropTech ──
    {"name": "Rental Agreement Automation", "domain": "PropTech", "type": "Technical",
     "tagline": "Digital, legally-valid rental agreements with e-sign and auto-registration in <10 min"},
    {"name": "Co-Living Discovery Platform", "domain": "PropTech", "type": "Non-Technical",
     "tagline": "Find and book co-living spaces with verified reviews and transparent pricing"},

    # ── Government / GovTech ──
    {"name": "Subsidy Eligibility Checker", "domain": "GovTech", "type": "Non-Technical",
     "tagline": "Tells citizens which government schemes they qualify for based on their profile"},
    {"name": "Municipal Complaint Tracker", "domain": "GovTech", "type": "Non-Technical",
     "tagline": "Citizens file and track grievances with the municipality; officials get an SLA dashboard"},

    # ── Content / Creator Economy ──
    {"name": "Micro-Newsletter Platform for India", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Launch a paid newsletter in 5 minutes with UPI payments and subscriber analytics"},
    {"name": "AI Scriptwriter for Reels", "domain": "Creator", "type": "Technical",
     "tagline": "Generates short-form video scripts with hooks, value, and CTAs based on your niche"},
    {"name": "Podcast Monetisation Platform", "domain": "Creator", "type": "Non-Technical",
     "tagline": "Helps Indian podcasters monetise via dynamic ads, listener support, and brand deals"},

    # ── AgriTech ──
    {"name": "Soil Health Monitoring App", "domain": "AgriTech", "type": "Technical",
     "tagline": "IoT soil sensors + AI recommendations for optimal fertiliser and irrigation"},
    {"name": "Mandi Price Tracker", "domain": "AgriTech", "type": "Non-Technical",
     "tagline": "Real-time wholesale mandi prices via WhatsApp for farmers across 500+ mandis"},
    {"name": "Farm-to-Restaurant Marketplace", "domain": "AgriTech", "type": "Non-Technical",
     "tagline": "Connects farmers directly with restaurants for fresh produce with no middlemen"},

    # ── Developer Tools ──
    {"name": "API Testing Copilot", "domain": "DevTools", "type": "Technical",
     "tagline": "AI that writes and runs API test cases from a Swagger/OpenAPI spec automatically"},
    {"name": "Code Review Bot for PRs", "domain": "DevTools", "type": "Technical",
     "tagline": "Reviews GitHub PRs for security, performance, and code quality using LLMs"},
    {"name": "Database Migration Assistant", "domain": "DevTools", "type": "Technical",
     "tagline": "AI-powered tool that plans, executes, and validates database schema migrations safely"},

    # ── Sustainability ──
    {"name": "Carbon Footprint Tracker for SMEs", "domain": "CleanTech", "type": "Non-Technical",
     "tagline": "Tracks Scope 1, 2, and 3 emissions for small businesses and generates ESG reports"},
    {"name": "EV Fleet Management Platform", "domain": "CleanTech", "type": "Technical",
     "tagline": "Manages EV charging schedules, battery health, and route optimisation for fleets"},

    # ── Social Impact ──
    {"name": "Donation Transparency Platform", "domain": "Social Impact", "type": "Non-Technical",
     "tagline": "NGOs publish real-time impact updates; donors track exactly where their money went"},
    {"name": "Rural Job Board", "domain": "Social Impact", "type": "Non-Technical",
     "tagline": "Hyperlocal job listings for rural workers in agriculture, construction, and services"},

    # ── InsurTech ──
    {"name": "Parametric Crop Insurance Platform", "domain": "InsurTech", "type": "Technical",
     "tagline": "Automatic payouts triggered by satellite rainfall data — no claim filing required"},
    {"name": "Pay-Per-KM Vehicle Insurance", "domain": "InsurTech", "type": "Technical",
     "tagline": "Telematics-based motor insurance that charges only for kilometres driven"},
    {"name": "Group Health Insurance for Gig Workers", "domain": "InsurTech", "type": "Non-Technical",
     "tagline": "Affordable pooled health cover for platform workers via their aggregator apps"},
    {"name": "Insurance Policy Aggregator", "domain": "InsurTech", "type": "Non-Technical",
     "tagline": "Compare and buy all insurance products from one dashboard with AI-assisted advice"},

    # ── RetailTech / D2C ──
    {"name": "Kirana Store Digitisation Kit", "domain": "RetailTech", "type": "Non-Technical",
     "tagline": "WhatsApp catalogue, digital billing, and UPI collections for neighbourhood stores"},
    {"name": "D2C Brand Analytics Platform", "domain": "RetailTech", "type": "Technical",
     "tagline": "Unified dashboard for D2C brands tracking CAC, LTV, and returns across Shopify and marketplaces"},
    {"name": "Live Commerce Platform for India", "domain": "RetailTech", "type": "Technical",
     "tagline": "Instagram-style live selling with one-tap UPI checkout for small brands"},
    {"name": "AI-Powered Personal Stylist App", "domain": "RetailTech", "type": "Technical",
     "tagline": "Camera-first app that builds your digital wardrobe and suggests outfits with affiliate shopping"},
    {"name": "Returns Management SaaS for eCommerce", "domain": "RetailTech", "type": "Technical",
     "tagline": "Automates return approvals, refunds, and restocking decisions to cut reverse logistics costs"},

    # ── TravelTech ──
    {"name": "Budget Trip Planner for India", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "AI itinerary builder for domestic travel optimised by budget, duration, and interests"},
    {"name": "Corporate Travel Management SaaS", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Policy-compliant booking + expense reporting for mid-market companies"},
    {"name": "Homestay Network for Rural Tourism", "domain": "TravelTech", "type": "Non-Technical",
     "tagline": "Verified rural homestay discovery platform with local experience bookings"},

    # ── FoodTech ──
    {"name": "Cloud Kitchen OS", "domain": "FoodTech", "type": "Technical",
     "tagline": "Operations platform for cloud kitchens managing orders across Swiggy, Zomato, and ONDC"},
    {"name": "Restaurant Menu Optimiser", "domain": "FoodTech", "type": "Technical",
     "tagline": "AI that analyses sales data to recommend menu changes that boost margins"},
    {"name": "Tiffin Service Marketplace", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Connects home cooks offering daily tiffin with office workers and students nearby"},
    {"name": "Food Safety Compliance App", "domain": "FoodTech", "type": "Non-Technical",
     "tagline": "Guides FSSAI licensing, audit checklists, and compliance calendar for F&B businesses"},

    # ── WealthTech ──
    {"name": "Goal-Based SIP Advisor", "domain": "WealthTech", "type": "Technical",
     "tagline": "Maps life goals (child's education, home, retirement) to optimal SIP portfolios"},
    {"name": "Fractional Real Estate Investment Platform", "domain": "WealthTech", "type": "Technical",
     "tagline": "Invest in Grade-A commercial real estate from ₹10,000 with monthly rental income"},
    {"name": "P2P Lending Marketplace", "domain": "WealthTech", "type": "Technical",
     "tagline": "RBI-regulated platform connecting retail lenders with creditworthy borrowers for 12–18% returns"},
    {"name": "Tax Filing Copilot for Freelancers", "domain": "WealthTech", "type": "Technical",
     "tagline": "Auto-fills ITR from bank statements and invoices; maximises deductions for self-employed"},

    # ── LegalTech ──
    {"name": "Online Court Date Tracker", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "Notifies litigants and lawyers of upcoming hearing dates with case status updates"},
    {"name": "Startup Legal Pack Generator", "domain": "LegalTech", "type": "Technical",
     "tagline": "Generates founder agreements, ESOP policy, and NDAs for early-stage startups in minutes"},
    {"name": "Arbitration Case Management Platform", "domain": "LegalTech", "type": "Non-Technical",
     "tagline": "End-to-end digital workspace for arbitrators, counsels, and parties in commercial disputes"},

    # ── ClimaTech / Sustainability ──
    {"name": "Green Building Certification Tracker", "domain": "CleanTech", "type": "Non-Technical",
     "tagline": "Guides builders through IGBC/LEED certification with documentation checklists and audits"},
    {"name": "Rooftop Solar Marketplace", "domain": "CleanTech", "type": "Non-Technical",
     "tagline": "Compare quotes from verified solar installers and track savings post-installation"},
    {"name": "Plastic Waste Collection Incentive App", "domain": "CleanTech", "type": "Non-Technical",
     "tagline": "Citizens earn reward points for depositing dry waste at collection centres"},

    # ── Developer Tools (extended) ──
    {"name": "CI/CD Pipeline Health Monitor", "domain": "DevTools", "type": "Technical",
     "tagline": "Tracks build success rates, flaky tests, and deployment frequency across all pipelines"},
    {"name": "Incident Post-Mortem Assistant", "domain": "DevTools", "type": "Technical",
     "tagline": "AI-assisted tool that drafts post-mortems from alert logs, chat history, and runbooks"},
    {"name": "Feature Flag Management Platform", "domain": "DevTools", "type": "Technical",
     "tagline": "Gradual rollouts, A/B testing, and kill switches for engineering teams without code deploys"},

    # ── HRTech (extended) ──
    {"name": "Internal Mobility Platform", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Matches employees to open internal roles based on skills, tenure, and career goals"},
    {"name": "Payroll Compliance Automation", "domain": "HRTech", "type": "Technical",
     "tagline": "Auto-calculates PF, ESI, PT, and TDS; files statutory returns across all states"},
    {"name": "360 Feedback & OKR Platform", "domain": "HRTech", "type": "Non-Technical",
     "tagline": "Lightweight performance management combining continuous feedback with goal tracking"},

    # ── EdTech (extended) ──
    {"name": "AI-Powered Mock Interview Platform", "domain": "EdTech", "type": "Technical",
     "tagline": "Video-based mock interviews with AI feedback on content, delivery, and body language"},
    {"name": "Coding Bootcamp Management SaaS", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Curriculum, cohort, and outcomes management platform for coding bootcamps and ISA schools"},
    {"name": "College Admission Counselling App", "domain": "EdTech", "type": "Non-Technical",
     "tagline": "Guides Class 12 students through college selection, application, and scholarship process"},

]  # end _UNUSED_START


# ─────────────────────────────────────────────────────────────────────────────
# PRD section structure
# ─────────────────────────────────────────────────────────────────────────────

def _deterministic_product_for_date(target_date: date) -> dict:
    """
    PRODUCTS list is complexity-ordered (index 0 = simplest, index 364 = most complex).
    Day 1 of the year maps to index 0, day 365 to index 364.
    This guarantees complexity increases throughout the year with zero repetitions.
    Same date always returns the same product.
    """
    day_of_year = target_date.timetuple().tm_yday  # 1–366
    idx = (day_of_year - 1) % len(PRODUCTS)
    return PRODUCTS[idx]


def _generate_prd_with_gemini(product: dict) -> dict | None:
    """Generate PRD using Gemini 2.0 Flash. Returns None if unavailable."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        # Try loading from .env manually
        env_path = os.path.join(BASE_DIR, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping Gemini generation")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # Try models in order of preference
        working_model = None
        for m in ["gemini-flash-lite-latest", "gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                test = genai.GenerativeModel(m)
                test.generate_content("ping", generation_config=genai.types.GenerationConfig(max_output_tokens=5))
                working_model = m
                break
            except Exception:
                continue
        if not working_model:
            return None
        model = genai.GenerativeModel(working_model)
        logger.info("Using Gemini model: %s", working_model)

        prompt = f"""You are a world-class Senior Product Manager with deep expertise in the {product['domain']} domain.
Write a detailed, realistic, domain-specific PRD for the following product.

Product: {product['name']}
Domain: {product['domain']}
Type: {product['type']} product
One-liner: {product['tagline']}

Be specific to the domain — use real industry terminology, realistic persona names, actual pain points, and credible metrics.
For technical products, suggest the actual tech stack. For non-technical products, suggest no-code or lean approaches.
India market context: pricing in INR, reference Indian regulations where relevant (RBI, SEBI, DPDP Act, etc.).

Return ONLY valid JSON with exactly these keys (no markdown, no explanation):
{{
  "problem_statement": "3 sentences: current pain, why existing solutions fail, what this product uniquely solves",
  "target_users": [
    "Persona 1 name + role + company type + specific pain point",
    "Persona 2 name + role + company type + specific pain point",
    "Persona 3 name + role + company type + specific pain point"
  ],
  "user_stories": [
    "As a [specific persona], I want to [specific action] so that [specific measurable benefit]",
    "As a [specific persona], I want to [specific action] so that [specific measurable benefit]",
    "As a [specific persona], I want to [specific action] so that [specific measurable benefit]",
    "As a [specific persona], I want to [specific action] so that [specific measurable benefit]",
    "As a [specific persona], I want to [specific action] so that [specific measurable benefit]"
  ],
  "mvp_features": [
    "Feature name: specific description of what it does and why it matters",
    "Feature name: specific description",
    "Feature name: specific description",
    "Feature name: specific description",
    "Feature name: specific description",
    "Feature name: specific description"
  ],
  "future_features": [
    "Future feature 1 with brief rationale",
    "Future feature 2 with brief rationale",
    "Future feature 3 with brief rationale",
    "Future feature 4 with brief rationale"
  ],
  "success_metrics": [
    "Metric name: specific target value by specific timeframe — why this metric matters",
    "Metric name: specific target value by specific timeframe — why this metric matters",
    "Metric name: specific target value by specific timeframe — why this metric matters",
    "Metric name: specific target value by specific timeframe — why this metric matters"
  ],
  "assumptions": [
    "Specific assumption about users, market, or technology",
    "Specific assumption about users, market, or technology",
    "Specific assumption about users, market, or technology"
  ],
  "risks": [
    "Specific risk description → specific mitigation strategy",
    "Specific risk description → specific mitigation strategy",
    "Specific risk description → specific mitigation strategy"
  ],
  "go_to_market": "3 sentences: launch channel, acquisition strategy, first 100 customer playbook",
  "tech_stack_suggestion": "3 sentences: specific technologies, architecture pattern, key integrations for this domain",
  "monetisation": "3 sentences: specific pricing tiers in INR, revenue model rationale, expansion revenue strategy",
  "timeline_weeks": {{"discovery": 2, "design": 2, "mvp_build": 8, "beta": 4, "launch": 2}}
}}"""

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=4096,
            )
        )
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            logger.info("PRD generated via Gemini 2.0 Flash for: %s", product["name"])
            return result
    except Exception as e:
        logger.warning("Gemini PRD generation failed: %s", e)
    return None


def _generate_prd_with_ollama(product: dict) -> dict | None:
    """Try to generate PRD sections via Ollama/Mistral. Returns None if unavailable."""
    try:
        import requests
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "mistral", "prompt": f"Write a PRD JSON for: {product['name']}", "stream": False},
            timeout=10,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
    except Exception as e:
        logger.debug("Ollama unavailable: %s", e)
    return None


def _generate_prd_template(product: dict) -> dict:
    """Fallback: structured template PRD based on product domain."""
    name = product["name"]
    domain = product["domain"]
    tagline = product["tagline"]
    is_tech = product["type"] == "Technical"

    return {
        "problem_statement": (
            f"Users in the {domain} space currently face significant friction with {tagline.lower()}. "
            f"Existing solutions are either too expensive, too complex, or not tailored to the Indian market. "
            f"{name} addresses this gap with a focused, user-first approach."
        ),
        "target_users": [
            f"Primary: Decision-makers in {domain} who face this problem daily",
            "Secondary: Operations teams who execute and track outcomes",
            "Tertiary: Analysts and managers who need reporting and insights",
        ],
        "user_stories": [
            f"As a primary user, I want to access {name} in under 30 seconds so that I don't lose momentum",
            "As a user, I want to see clear results so that I can make decisions with confidence",
            "As an admin, I want to configure settings without engineering help so that I stay autonomous",
            "As a manager, I want a dashboard view so that I can track team-level outcomes",
            "As a new user, I want onboarding guidance so that I get value within the first session",
        ],
        "mvp_features": [
            "Core workflow: End-to-end execution of the primary use case with minimal clicks",
            "User authentication: Email + Google SSO with role-based access",
            "Dashboard: Key metrics and recent activity in a single view",
            "Export: Download results as PDF and CSV",
            "Notifications: Email and in-app alerts for key events",
            "Mobile responsive: Full functionality on mobile browsers",
        ],
        "future_features": [
            "API access for enterprise integrations",
            "AI-powered recommendations and automation",
            "White-label / custom branding for B2B customers",
            "Advanced analytics and custom reporting",
        ],
        "success_metrics": [
            "Activation rate: 60%+ of sign-ups complete the core workflow in session 1",
            "D7 retention: 40%+ of activated users return within 7 days",
            "NPS: Score of 50+ by month 3",
            "Time-to-value: Core task completed in under 5 minutes for 80% of users",
        ],
        "assumptions": [
            "Users have a smartphone or laptop with internet access",
            f"The {domain} market in India has sufficient TAM to justify the product",
            "Unit economics are viable at the target price point",
        ],
        "risks": [
            "Low adoption risk → mitigate with strong onboarding, free trial, and white-glove support for first 50 customers",
            "Competition from incumbents → differentiate on UX simplicity and India-specific features",
            "Regulatory risk (if applicable) → engage compliance counsel early and build compliance-first",
        ],
        "go_to_market": (
            f"Launch with a waitlist and 50 beta users recruited from {domain} communities on LinkedIn and WhatsApp. "
            "Use a freemium model to drive top-of-funnel, then convert via usage-based gating. "
            "Invest in SEO-rich content targeting high-intent search terms in the domain."
        ),
        "tech_stack_suggestion": (
            f"{'Next.js + FastAPI + Supabase' if is_tech else 'No-code first: Bubble or Webflow for MVP'} "
            f"to move fast. Use {'Vercel for deployment and' if is_tech else ''} cloud-hosted DB for scalability. "
            "Integrate with existing tools via Zapier/webhooks for B2B customers."
        ),
        "monetisation": (
            "Freemium: free tier with usage limits to drive acquisition. "
            "Pro plan at ₹999/month for individuals, ₹4,999/month for teams up to 10. "
            "Enterprise: custom pricing with SLA, SSO, and dedicated support."
        ),
        "timeline_weeks": {
            "discovery": 2,
            "mvp_build": 8,
            "beta": 4,
            "launch": 2,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_daily_prd(target_date: date | None = None) -> dict:
    """
    Generate (or load cached) PRD for the given date.
    Returns the full PRD dict including metadata.
    """
    if target_date is None:
        target_date = date.today()

    date_str = target_date.isoformat()
    cache_path = os.path.join(PRD_DIR, f"prd_{date_str}.json")

    # Return cached PRD if already generated today
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    product = _deterministic_product_for_date(target_date)

    # Try Gemini first, then Ollama, then template fallback
    sections = _generate_prd_with_gemini(product)
    generated_by = "gemini-2.0-flash"
    if sections is None:
        sections = _generate_prd_with_ollama(product)
        generated_by = "ollama"
    if sections is None:
        sections = _generate_prd_template(product)
        generated_by = "template"

    prd = {
        "id": date_str,
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "generated_by": generated_by,
        "product": product,
        "sections": sections,
    }

    with open(cache_path, "w") as f:
        json.dump(prd, f, indent=2)

    logger.info("PRD generated for %s: %s (via %s)", date_str, product["name"], generated_by)
    return prd


def list_prds() -> list[dict]:
    """Return all saved PRDs sorted newest first (metadata only)."""
    prds = []
    for fname in sorted(os.listdir(PRD_DIR), reverse=True):
        if fname.startswith("prd_") and fname.endswith(".json"):
            try:
                with open(os.path.join(PRD_DIR, fname)) as f:
                    prd = json.load(f)
                prds.append({
                    "id": prd["id"],
                    "date": prd["date"],
                    "product_name": prd["product"]["name"],
                    "domain": prd["product"]["domain"],
                    "type": prd["product"]["type"],
                    "tagline": prd["product"]["tagline"],
                    "generated_by": prd.get("generated_by", "template"),
                })
            except Exception:
                pass
    return prds


def build_prd_email_html(prd: dict) -> str:
    """
    Mobile-first, minimal PRD email — 600px max-width, email-safe inline CSS.
    15-section structure matching the standard PRD template.
    """
    p  = prd["product"]
    s  = prd["sections"]
    dt = datetime.fromisoformat(prd["date"]).strftime("%B %d, %Y")

    # ── Palette ──────────────────────────────────────────────────────────────
    C = {
        "bg":       "#FFFFFF",
        "bg2":      "#F8FAFC",
        "bg3":      "#F1F5F9",
        "border":   "#E2E8F0",
        "border2":  "#CBD5E1",
        "text":     "#0F172A",
        "text2":    "#475569",
        "text3":    "#94A3B8",
        "accent":   "#0F172A",
        "pill_bg":  "#F1F5F9",
        "pill_txt": "#475569",
        "p0_bg":    "#FEF2F2", "p0_c": "#991B1B",
        "p1_bg":    "#FFFBEB", "p1_c": "#92400E",
        "p2_bg":    "#EFF6FF", "p2_c": "#1D4ED8",
        "p3_bg":    "#F8FAFC", "p3_c": "#64748B",
        "note_bg":  "#EFF6FF", "note_border": "#3B82F6", "note_c": "#1E40AF",
    }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def esc(t):
        return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def badge(label, variant="p3"):
        bgs  = {"p0":C["p0_bg"],"p1":C["p1_bg"],"p2":C["p2_bg"],"p3":C["p3_bg"]}
        clrs = {"p0":C["p0_c"], "p1":C["p1_c"], "p2":C["p2_c"], "p3":C["p3_c"]}
        return (f'<span style="display:inline-block;font-size:10px;font-weight:600;'
                f'padding:2px 9px;border-radius:99px;letter-spacing:0.03em;'
                f'background:{bgs.get(variant,C["p3_bg"])};'
                f'color:{clrs.get(variant,C["p3_c"])};">{esc(label)}</span>')

    def note(text):
        return (f'<div style="background:{C["note_bg"]};border-left:3px solid {C["note_border"]};'
                f'padding:10px 14px;border-radius:0 6px 6px 0;font-size:12px;'
                f'color:{C["note_c"]};margin-bottom:16px;line-height:1.5;">{text}</div>')

    def card(content, mb="12px"):
        return (f'<div style="background:{C["bg2"]};border-radius:8px;'
                f'padding:14px 16px;margin-bottom:{mb};font-size:14px;'
                f'line-height:1.7;color:{C["text"]};">{content}</div>')

    def field_block(label, content_html):
        return (f'<div style="margin-bottom:18px;">'
                f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;'
                f'letter-spacing:0.07em;color:{C["text3"]};margin-bottom:6px;">{esc(label)}</div>'
                f'{content_html}'
                f'</div>')

    def text_card(value, placeholder=False):
        st = f'font-style:italic;color:{C["text3"]};' if placeholder else f'color:{C["text"]};'
        return card(f'<span style="{st}font-size:14px;line-height:1.7;">{value}</span>')

    def bullet_list(items, muted=False):
        if not items:
            return card(f'<span style="color:{C["text3"]};font-style:italic;">—</span>')
        c = C["text2"] if muted else C["text"]
        rows = "".join(
            f'<tr><td style="padding:4px 0;vertical-align:top;width:16px;color:{C["text3"]};font-size:14px;">·</td>'
            f'<td style="padding:4px 0 4px 8px;font-size:14px;line-height:1.6;color:{c};">{esc(i)}</td></tr>'
            for i in items
        )
        return f'<div style="background:{C["bg2"]};border-radius:8px;padding:14px 16px;margin-bottom:12px;"><table style="width:100%;border-collapse:collapse;">{rows}</table></div>'

    def divider():
        return f'<div style="height:1px;background:{C["border"]};margin:28px 0;"></div>'

    def section_header(num, title):
        return (f'<div style="margin-bottom:16px;">'
                f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:0.10em;color:{C["text3"]};margin-bottom:4px;">SECTION {num:02d}</div>'
                f'<div style="font-size:19px;font-weight:600;color:{C["text"]};'
                f'letter-spacing:-0.01em;">{title}</div>'
                f'</div>')

    def kpi_block(metrics):
        if not metrics:
            return ""
        cells = ""
        for i, m in enumerate(metrics[:3]):
            label = ["Primary KPI","Secondary KPI","Guardrail"][i]
            val   = (m.split(":")[0] if ":" in m else m[:45]).strip()
            note_t = (m.split(":",1)[1].strip() if ":" in m else "")[:80]
            cells += (f'<td style="padding:0;width:33%;vertical-align:top;">'
                      f'<div style="background:{C["bg2"]};border-radius:8px;padding:14px;margin:0 4px;">'
                      f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:0.07em;'
                      f'color:{C["text3"]};margin-bottom:4px;">{label}</div>'
                      f'<div style="font-size:14px;font-weight:600;color:{C["text"]};margin-bottom:2px;">{esc(val)}</div>'
                      f'<div style="font-size:11px;color:{C["text3"]};">{esc(note_t)}</div>'
                      f'</div></td>')
        return (f'<table style="width:100%;border-collapse:collapse;margin-bottom:18px;">'
                f'<tr>{cells}</tr></table>')

    def us_card(num, priority, story, ac=""):
        v = {"P0":"p0","P1":"p1","P2":"p2"}.get(priority,"p2")
        return (f'<div style="border:1px solid {C["border"]};border-radius:8px;'
                f'padding:14px 16px;margin-bottom:10px;">'
                f'<div style="margin-bottom:6px;">'
                f'<span style="font-size:10px;font-weight:700;color:{C["text3"]};'
                f'text-transform:uppercase;letter-spacing:0.07em;margin-right:8px;">US-{num:02d}</span>'
                f'{badge(priority, v)}</div>'
                f'<div style="font-size:14px;color:{C["text"]};line-height:1.6;margin-bottom:4px;">{esc(story)}</div>'
                f'<div style="font-size:12px;color:{C["text2"]};">{esc(ac)}</div>'
                f'</div>')

    def tl_row(phase, detail):
        return (f'<tr>'
                f'<td style="padding:8px 12px 8px 0;vertical-align:top;white-space:nowrap;width:110px;">'
                f'<span style="display:inline-block;font-size:11px;font-weight:600;'
                f'background:{C["bg2"]};color:{C["text2"]};padding:4px 10px;'
                f'border-radius:6px;white-space:nowrap;">{esc(phase)}</span></td>'
                f'<td style="padding:8px 0;font-size:13px;color:{C["text2"]};'
                f'line-height:1.5;vertical-align:top;">{esc(detail)}</td>'
                f'</tr>')

    def risk_table(risks):
        if not risks:
            return ""
        header = (f'<tr>'
                  f'<th style="text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;'
                  f'letter-spacing:0.07em;color:{C["text3"]};padding:0 8px 8px 0;border-bottom:1px solid {C["border"]};">Risk</th>'
                  f'<th style="text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;'
                  f'letter-spacing:0.07em;color:{C["text3"]};padding:0 0 8px 8px;border-bottom:1px solid {C["border"]};">Mitigation</th>'
                  f'</tr>')
        rows = ""
        for r in risks:
            parts = (r.split("→") if "→" in r
                     else r.split(" — ") if " — " in r
                     else r.split(" mitigate") if " mitigate" in r.lower()
                     else [r, "See risk register"])
            risk_t = parts[0].strip()
            mit_t  = parts[-1].strip() if len(parts) > 1 else "Mitigation plan TBD"
            rows += (f'<tr>'
                     f'<td style="padding:10px 8px 10px 0;font-size:13px;font-weight:500;'
                     f'color:{C["text"]};vertical-align:top;border-bottom:1px solid {C["border"]};">{esc(risk_t)}</td>'
                     f'<td style="padding:10px 0 10px 8px;font-size:13px;color:{C["text2"]};'
                     f'vertical-align:top;border-bottom:1px solid {C["border"]};">{esc(mit_t)}</td>'
                     f'</tr>')
        return f'<table style="width:100%;border-collapse:collapse;">{header}{rows}</table>'

    def data_table(headers, rows):
        th = "".join(
            f'<th style="text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.07em;color:{C["text3"]};padding:0 12px 8px 0;'
            f'border-bottom:1px solid {C["border"]};">{esc(h)}</th>'
            for h in headers
        )
        body = ""
        for row in rows:
            tds = "".join(
                f'<td style="padding:9px 12px 9px 0;font-size:13px;color:{C["text2"] if i>0 else C["text"]};'
                f'{"font-weight:500;" if i==0 else ""}vertical-align:top;'
                f'border-bottom:1px solid {C["border"]};">{esc(c) if not str(c).startswith("<") else c}</td>'
                for i, c in enumerate(row)
            )
            body += f'<tr>{tds}</tr>'
        return f'<table style="width:100%;border-collapse:collapse;"><tr>{th}</tr>{body}</table>'

    # ── Section data ──────────────────────────────────────────────────────────
    ps        = s.get("problem_statement", "")
    us_list   = s.get("user_stories", [])
    mvp       = s.get("mvp_features", [])
    future_f  = s.get("future_features", [])
    risks_l   = s.get("risks", [])
    tl        = s.get("timeline_weeks", {})
    metrics   = s.get("success_metrics", [])
    personas  = s.get("target_users", [])
    assump    = s.get("assumptions", [])
    gtm       = s.get("go_to_market", "")
    tech      = s.get("tech_stack_suggestion", "")
    mon       = s.get("monetisation", "")

    # ── Build sections ────────────────────────────────────────────────────────

    sections_html = ""

    # 1 — Executive Summary
    sections_html += divider()
    sections_html += section_header(1, "Executive Summary")
    sections_html += field_block("One-liner", text_card(esc(p["tagline"])))
    sections_html += field_block("Background",
        text_card(f'The {esc(p["domain"])} space faces structural inefficiency — '
                  f'{esc(p["tagline"].lower())}. As digital adoption accelerates in India, '
                  f'the gap between user expectations and available solutions has grown, '
                  f'creating a clear opportunity for a focused, user-first product.'))
    sections_html += field_block("Proposed solution", text_card(esc(ps)))
    sections_html += field_block("Expected impact",
        text_card(esc(metrics[0]) if metrics else "Measurable improvement in efficiency and satisfaction within 90 days of launch."))

    # 2 — Problem Statement
    sections_html += divider()
    sections_html += section_header(2, "Problem Statement")
    sections_html += field_block("Problem description", text_card(esc(ps)))
    sections_html += field_block("Current state / workarounds",
        text_card(f'Users currently rely on manual processes, spreadsheets, or fragmented point solutions — '
                  f'none of which address the core workflow efficiently in the {esc(p["domain"])} space.'))
    sections_html += field_block("Problem quantification",
        text_card(esc(metrics[1]) if len(metrics)>1
                  else "Est. 4–8 hours of manual work per user per week; significant error rate in current workarounds."))
    sections_html += field_block("Root cause",
        text_card(esc(assump[0]) if assump
                  else f'Absence of a purpose-built tool for the {esc(p["domain"])} segment in the Indian market.'))

    # 3 — Goals & Success Metrics
    sections_html += divider()
    sections_html += section_header(3, "Goals & Success Metrics")
    sections_html += note("Define goals before features. Metrics must be measurable and time-bound.")
    sections_html += field_block("Business goals", text_card(esc(gtm) if gtm else "—"))
    sections_html += field_block("User goals",
        text_card(esc(us_list[0]) if us_list else "Complete the core task faster with fewer errors."))
    sections_html += field_block("Success metrics (KPIs)", kpi_block(metrics))
    sections_html += field_block("Non-goals",
        text_card("Enterprise-scale deployment in v1, international markets, or feature parity with incumbents. "
                  "Speed-to-value for the primary persona takes precedence."))

    # 4 — Personas
    sections_html += divider()
    sections_html += section_header(4, "Users & Personas")
    persona_rows = []
    for i, pname in enumerate(personas[:4]):
        clean = pname.split(":",1)[1].strip() if ":" in pname else pname
        label = pname.split(":")[0].strip() if ":" in pname else f"Persona {chr(65+i)}"
        pri   = [badge("Primary","p0"), badge("Secondary","p1"),
                 badge("Tertiary","p3"), badge("Adjacent","p3")][min(i,3)]
        persona_rows.append([label, clean, "Core job-to-be-done", pri])
    sections_html += data_table(["Persona","Description","Primary need","Priority"], persona_rows)

    # 5 — Scope
    sections_html += divider()
    sections_html += section_header(5, "Scope & Non-Scope")
    sections_html += field_block("In scope (v1)", bullet_list(mvp))
    sections_html += field_block("Out of scope", bullet_list(future_f, muted=True))
    sections_html += field_block("Future phases (parking lot)",
        text_card("API marketplace, white-label offering, advanced analytics, multi-language support — "
                  "deferred pending product-market fit validation.", placeholder=True))

    # 6 — User Stories
    sections_html += divider()
    sections_html += section_header(6, "User Stories")
    sections_html += note("Format: As a [persona], I want to [action] so that [outcome].")
    priorities = ["P0","P0","P1","P1","P2"]
    for i, story in enumerate(us_list[:5]):
        sections_html += us_card(i+1, priorities[min(i,4)], story,
                                 "Acceptance criteria: feature works end-to-end / edge cases handled / performance within threshold")

    # 7 — Functional Requirements
    sections_html += divider()
    sections_html += section_header(7, "Functional Requirements")
    pri_map = ["p0","p0","p1","p1","p2","p3"]
    pri_lbl = ["Must have","Must have","Should have","Should have","Could have","Won't have (v1)"]
    fr_rows = []
    for i, feat in enumerate(mvp[:6]):
        req = feat.split(":",1)[1].strip() if ":" in feat else feat
        fr_rows.append([f"FR-{i+1:02d}", req, badge(pri_lbl[min(i,5)], pri_map[min(i,5)]), "—"])
    sections_html += data_table(["ID","Requirement","Priority","Notes"], fr_rows)

    # 8 — NFR
    sections_html += divider()
    sections_html += section_header(8, "Non-Functional Requirements")
    nfr_rows = [
        ["Performance",   "API response time (p95)",         "< 500ms"],
        ["Scalability",   "Concurrent users",                 "500 at launch → 5,000 by month 6"],
        ["Availability",  "Uptime SLA",                       "99.5% MVP → 99.9% post-PMF"],
        ["Security",      "Auth + encryption",                "OAuth 2.0 / JWT; AES-256 at rest"],
        ["Compliance",    "Regulatory",
         "DPDP Act 2023" + ("; RBI guidelines" if any(w in p["domain"].lower() for w in ["fintech","banking"]) else "")],
        ["Accessibility", "WCAG",                             "AA minimum"],
        ["Localization",  "Languages",                        "English + Hindi (v1)"],
    ]
    sections_html += data_table(["Category","Requirement","Target"], nfr_rows)

    # 9 — UX
    sections_html += divider()
    sections_html += section_header(9, "UX & Design Notes")
    sections_html += field_block("Key user flows",
        text_card("1. Onboarding &amp; first value moment (target: &lt;3 min) &nbsp;·&nbsp; "
                  "2. Core task execution &nbsp;·&nbsp; 3. Result review &amp; export &nbsp;·&nbsp; 4. Error recovery"))
    sections_html += field_block("Design constraints",
        text_card("Web-first + mobile-responsive; existing design system; "
                  "accessible on low-bandwidth connections; supports light and dark mode."))
    sections_html += field_block("Empty states &amp; error handling",
        text_card("Empty state: contextual illustration + primary CTA. "
                  "API error: toast with retry. Permission error: inline explainer with upgrade path."))

    # 10 — Technical
    sections_html += divider()
    sections_html += section_header(10, "Technical Considerations")
    sections_html += field_block("System architecture",
        text_card("Stateless REST API backend; event-driven async for heavy tasks; "
                  "CDN for static assets; modular monolith for v1 (microservice-ready)."))
    sections_html += field_block("APIs &amp; integrations", text_card(esc(tech) if tech else "—"))
    sections_html += field_block("Data model",
        text_card(f'New entities: User, Session, {p["name"].replace(" ","")}Record. '
                  f'Schema versioned from day 1. No breaking migrations in v1.'))
    sections_html += field_block("Known constraints",
        text_card("Monolith limits horizontal scaling beyond ~1,000 concurrent users — "
                  "acceptable for v1. Refactor planned for v2 based on load data."))

    # 11 — Timeline
    sections_html += divider()
    sections_html += section_header(11, "Timeline & Milestones")
    phase_notes = {
        "discovery":  "User research, problem validation, stakeholder alignment",
        "design":     "Wireframes, prototypes, design review",
        "mvp_build":  "Core backend + frontend build",
        "mvp build":  "Core backend + frontend build",
        "beta":       "Beta testing, bug fixes, UAT",
        "launch":     "GA release, launch comms, adoption tracking",
    }
    tl_rows_html = ""
    week = 1
    phases = tl.items() if tl else [("Discovery",2),("Design",2),("MVP Build",8),("Beta",4),("Launch",2)]
    for phase, weeks in phases:
        detail = f"Week {week}–{week+weeks-1} ({weeks}w) — " + phase_notes.get(str(phase).lower().replace(" ","_"), f"{weeks}-week phase")
        tl_rows_html += tl_row(str(phase).replace("_"," ").title(), detail)
        week += weeks
    sections_html += f'<table style="width:100%;border-collapse:collapse;">{tl_rows_html}</table>'

    # 12 — Risks
    sections_html += divider()
    sections_html += section_header(12, "Risks & Mitigations")
    all_risks = list(risks_l) + [
        "User adoption risk → In-app tooltips, launch campaign, 30-day adoption review with PM",
        "Scope creep → Hard scope freeze post-PRD sign-off; changes through formal change log",
    ]
    sections_html += risk_table(all_risks[:6])

    # 13 — Dependencies
    sections_html += divider()
    sections_html += section_header(13, "Dependencies")
    dep_rows = [
        ["Payment gateway",   "External API",    "Finance team",    "Confirmed"],
        ["Auth service",      "Internal",        "Platform team",   "In progress"],
        ["Design system",     "Internal",        "Design team",     "Available"],
    ]
    if any(w in p["domain"].lower() for w in ["fintech","banking","health","legal"]):
        dep_rows.append(["Regulatory approval","Compliance","Legal","Pending"])
    sections_html += data_table(["Dependency","Type","Owner","Status"], dep_rows)

    # 14 — Open Questions
    sections_html += divider()
    sections_html += section_header(14, "Open Questions")
    oq_rows = [
        ["Q1", f'Should {esc(future_f[0]) if future_f else "advanced analytics"} be in v1 or deferred?', "PM",    "Open"],
        ["Q2", "What is the data retention policy for user-generated content?",                             "Legal", "Open"],
        ["Q3", "How do we handle existing users during onboarding migration?",                              "Eng",   "Open"],
    ]
    if assump:
        oq_rows.append(["Q4", esc(assump[0]), "PM", "Open"])
    sections_html += data_table(["#","Question","Owner","Status"], oq_rows)

    # 15 — Appendix
    sections_html += divider()
    sections_html += section_header(15, "Appendix")
    sections_html += field_block("Change log",
        text_card(f"v1.0 — {esc(dt)} — Initial draft (auto-generated by Daily PRD · Job Search Agent)"))
    sections_html += field_block("Monetisation notes", text_card(esc(mon) if mon else "—"))
    sections_html += field_block("Glossary",
        text_card("MVP: Minimum Viable Product &nbsp;·&nbsp; PRD: Product Requirements Document &nbsp;·&nbsp; "
                  "PMF: Product-Market Fit &nbsp;·&nbsp; NFR: Non-Functional Requirement &nbsp;·&nbsp; UAT: User Acceptance Testing"))

    # ── TOC pills ─────────────────────────────────────────────────────────────
    toc_items = ["Executive Summary","Problem Statement","Goals & Metrics","Personas","Scope",
                 "User Stories","Functional Req.","NFR","UX Notes","Technical",
                 "Timeline","Risks","Dependencies","Open Questions","Appendix"]
    toc_pills = " ".join(
        f'<span style="display:inline-block;font-size:11px;padding:4px 10px;border-radius:99px;'
        f'background:{C["bg3"]};color:{C["text2"]};margin:3px 2px;white-space:nowrap;">{t}</span>'
        for t in toc_items
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PRD — {esc(p['name'])}</title>
  <style>
    body {{ margin:0; padding:0; background:#F8FAFC; }}
    @media only screen and (max-width:620px) {{
      .outer  {{ padding: 12px !important; }}
      .inner  {{ padding: 20px 16px !important; border-radius: 8px !important; }}
      .meta-table td {{ display:block !important; width:100% !important; padding-bottom:8px !important; }}
      .kpi-td   {{ display:block !important; width:100% !important; padding-bottom:8px !important; }}
      .kpi-div  {{ margin:0 0 8px 0 !important; }}
      .scope-td {{ display:block !important; width:100% !important; padding-bottom:12px !important; }}
      h1 {{ font-size:22px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#F8FAFC;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

<div class="outer" style="max-width:640px;margin:0 auto;padding:24px 16px;">
<div class="inner" style="background:#FFFFFF;border-radius:12px;padding:32px 36px;border:1px solid #E2E8F0;">

  <!-- ── Header ── -->
  <div style="margin-bottom:28px;">
    <div style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;
                color:{C['text3']};margin-bottom:10px;">
      📋 &nbsp;Daily PRD &nbsp;·&nbsp; {esc(dt)}
    </div>
    <h1 style="font-size:26px;font-weight:700;color:{C['text']};margin:0 0 8px 0;
               line-height:1.2;letter-spacing:-0.02em;">{esc(p['name'])}</h1>
    <p style="font-size:14px;color:{C['text2']};margin:0 0 16px 0;line-height:1.6;">{esc(p['tagline'])}</p>
    <div>
      <span style="display:inline-block;font-size:11px;font-weight:600;padding:4px 12px;
                   border-radius:99px;background:{C['bg2']};color:{C['text2']};margin-right:6px;">{esc(p['domain'])}</span>
      <span style="display:inline-block;font-size:11px;font-weight:600;padding:4px 12px;
                   border-radius:99px;background:{C['bg2']};color:{C['text2']};">{esc(p['type'])} Product</span>
    </div>
  </div>

  <!-- ── Meta grid ── -->
  <table class="meta-table" style="width:100%;border-collapse:collapse;margin-bottom:24px;">
    <tr>
      <td style="width:25%;padding:0 8px 0 0;vertical-align:top;">
        <div style="background:{C['bg2']};border-radius:8px;padding:12px 14px;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:{C['text3']};margin-bottom:3px;">Version</div>
          <div style="font-size:13px;font-weight:600;color:{C['text']};">v1.0 · Draft</div>
        </div>
      </td>
      <td style="width:25%;padding:0 8px;vertical-align:top;">
        <div style="background:{C['bg2']};border-radius:8px;padding:12px 14px;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:{C['text3']};margin-bottom:3px;">Domain</div>
          <div style="font-size:13px;font-weight:600;color:{C['text']};">{esc(p['domain'])}</div>
        </div>
      </td>
      <td style="width:25%;padding:0 8px;vertical-align:top;">
        <div style="background:{C['bg2']};border-radius:8px;padding:12px 14px;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:{C['text3']};margin-bottom:3px;">Type</div>
          <div style="font-size:13px;font-weight:600;color:{C['text']};">{esc(p['type'])}</div>
        </div>
      </td>
      <td style="width:25%;padding:0 0 0 8px;vertical-align:top;">
        <div style="background:{C['bg2']};border-radius:8px;padding:12px 14px;">
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:{C['text3']};margin-bottom:3px;">Date</div>
          <div style="font-size:13px;font-weight:600;color:{C['text']};">{esc(dt)}</div>
        </div>
      </td>
    </tr>
  </table>

  <!-- ── TOC ── -->
  <div style="border:1px solid {C['border']};border-radius:8px;padding:14px 16px;margin-bottom:8px;">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.09em;
                color:{C['text3']};margin-bottom:10px;">Contents</div>
    <div style="line-height:2;">{toc_pills}</div>
  </div>

  <!-- ── 15 Sections ── -->
  {sections_html}

  <!-- ── Footer ── -->
  <div style="margin-top:32px;padding-top:20px;border-top:1px solid {C['border']};
              text-align:center;color:{C['text3']};font-size:11px;line-height:1.7;">
    Daily PRD &nbsp;·&nbsp; {esc(dt)} &nbsp;·&nbsp; Job Search Agent<br>
    <span style="color:{C['text3']};">View all PRDs in the PRD Library tab of your dashboard</span>
  </div>

</div><!-- /inner -->
</div><!-- /outer -->

</body>
</html>"""
