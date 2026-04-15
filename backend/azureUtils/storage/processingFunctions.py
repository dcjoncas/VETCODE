def statusProcessing(statusEnum: int) -> str:
    if statusEnum == 1:
        return "Draft"
    elif statusEnum == 2:
        return "Pending"
    elif statusEnum == 3:
        return "Published"
    elif statusEnum == 4:
        return "Updated"
    
# Get the overall status of a candidate
def stepProcessingOverall(stepEnum: list[int]) -> str:
    # Ensure highest values are first
    for s in sorted(stepEnum, reverse=True):
        # Overall Status
        if s == 7:
            return 'Certified'
            
        if s == 6:
            return 'Onboarded'
            
        if s == 5:
            return 'Reviewed'
            
        if s == 4:
            return 'PreOnboarded'
            
        if s == 3:
            return 'Vetted'
            
        if s == 2:
            return 'Screened'
            
        if s == 1:
            return 'Identified'
            

'''Identified = 1,
    Screened = 2,
    Vetted = 3,
    PreOnboarded = 4,
    Reviewed = 5,
    Onboarded = 6,
    Certified = 7,
    ScreeningCall = 8,
    PreOnboardingCall = 9,
    CandidateCheckInCall = 10,
    TechnicalVettingCall = 11,
    OnboardingCall = 12,
    MemberCheckInCall = 13,
    AnticipatedScreeningCall = 14,
    Answered = 15,
    Updated = 16,
    Published = 17,
    NoteTalentAcquisition = 18,
    NoteTechnicalVetting = 19,
    NoteCommunity = 20,
    Unpublished = 21,'''

def leadSourceProcessing(leadSourceEnum: int) -> str:
    if leadSourceEnum == 1:
        return "Recruiter Sourcing"
    elif leadSourceEnum == 2:
        return "Candidate Referral"
    else:
        return "Other"
    
# TODO: figure out results enum processing:
'''public enum PlatformActivityResultEnum
{
    Complete = 1,
    Pass = 2,
    PassRole = 3,
    PassCommunity = 4,
    Fail = 5,
    QualityIssue = 6,
    Connected = 7,
    NoShow = 8,
    Late = 9,
    Rescheduled = 10,
    Canceled = 11,
    DateSet = 12,
    Documented = 13,
}'''